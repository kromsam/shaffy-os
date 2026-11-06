"""Battery policy validation and actual Ansible convergence in a disposable directory."""

import configparser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy import bootstrap
from shaffy.loader import SettingsError, load

ROLE = ROOT / 'roles/power'
POLICY = {'low_percent': 18, 'critical_percent': 9, 'action_percent': 4}


class PowerSettingsTests(unittest.TestCase):
    def test_default_keeps_the_packaged_policy(self):
        self.assertNotIn('power', load(ROOT, without=True)['shaffy']['settings'])

    def test_fixture_carries_the_policy(self):
        settings = load(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive')['shaffy']['settings']
        self.assertEqual(settings['power']['low_battery_hibernate'], POLICY)

    def test_invalid_percentages_are_refused_by_the_loader(self):
        for key, value in [('low_percent', 0), ('low_percent', 101), ('critical_percent', 18), ('action_percent', 10)]:
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as directory:
                policy = POLICY | {key: value}
                config = {
                    'schema_version': 1,
                    'supplement_id': 'battery-test',
                    'power': {'low_battery_hibernate': policy},
                }
                Path(directory, 'config.yaml').write_text(yaml.safe_dump(config))
                with self.assertRaises(SettingsError):
                    load(ROOT, supplement=directory)


@unittest.skipUnless(shutil.which('mise'), 'needs the Ansible controller')
class PowerRoleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.tmp = Path(self.temporary.name)
        self.config_dir = self.tmp / 'UPower.conf.d'
        self.config_dir.mkdir()
        self.override = self.config_dir / '90-shaffy-critical-battery.conf'
        # Exercise the production tasks and template without requiring root. Only
        # file ownership is omitted, and the service restart is replaced by an assertion.
        tasks = yaml.safe_load((ROLE / 'tasks/configure.yml').read_text())
        for task in tasks:
            for module in ('ansible.builtin.file', 'ansible.builtin.template', 'ansible.builtin.copy'):
                if module in task:
                    task[module].pop('owner', None)
                    task[module].pop('group', None)
            if 'ansible.builtin.template' in task:
                task['ansible.builtin.template']['src'] = str(ROLE / 'templates/critical-battery.conf.j2')
            if 'when' in task:
                task['when'] = task['when'].replace(
                    "'critical-battery.conf.j2'", repr(str(ROLE / 'templates/critical-battery.conf.j2'))
                )
            if 'ansible.builtin.systemd_service' in task:
                del task['ansible.builtin.systemd_service']
                task['ansible.builtin.assert'] = {
                    'that': 'not power_test_reload_fails',
                    'success_msg': 'policy reloaded',
                }
        self.configure = self.tmp / 'configure.yml'
        self.configure.write_text(yaml.safe_dump(tasks))

    def play(self, policy, tasks=None, environment=None, reload_fails=False):
        play = self.tmp / 'play.yml'
        play.write_text(
            yaml.safe_dump(
                [
                    {
                        'hosts': 'localhost',
                        'connection': 'local',
                        'gather_facts': False,
                        'vars': {
                            'ansible_python_interpreter': '/usr/bin/python3',
                            'power_hibernate': policy,
                            'power_config_dir': str(self.config_dir),
                            'power_state_dir': str(self.tmp),
                            'power_test_reload_fails': reload_fails,
                        },
                        'environment': environment or {},
                        'tasks': tasks or [{'ansible.builtin.import_tasks': str(self.configure)}],
                    }
                ]
            )
        )
        command, env = bootstrap.ansible_command(ROOT, 'ansible-playbook', '-i', 'localhost,', str(play))
        return subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True, timeout=90, check=False)

    def test_install_repeat_change_and_remove_preserve_other_configuration(self):
        other = self.config_dir / '20-vendor.conf'
        other.write_text('[UPower]\nIgnoreLid=false\n')
        first = self.play(POLICY)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        config = configparser.ConfigParser()
        config.read(self.override)
        self.assertEqual(config['UPower']['CriticalPowerAction'], 'Hibernate')
        self.assertEqual(config['UPower'].getfloat('PercentageAction'), 4)
        self.assertEqual(config['UPower'].getfloat('PercentageCritical'), 9)
        self.assertEqual(config['UPower'].getfloat('PercentageLow'), 18)
        self.assertTrue(config['UPower'].getboolean('UsePercentageForPolicy'))
        second = self.play(POLICY)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertRegex(second.stdout, r'changed=0\s')
        changed = self.play(POLICY | {'action_percent': 3.5})
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        self.assertIn('policy reloaded', changed.stdout)
        self.assertIn('PercentageAction=3.5', self.override.read_text())
        removed = self.play({})
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertFalse(self.override.exists())
        self.assertEqual(other.read_text(), '[UPower]\nIgnoreLid=false\n')
        repeated = self.play({})
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
        self.assertRegex(repeated.stdout, r'changed=0\s')

    def test_failed_reload_is_retried_after_install_and_removal(self):
        pending = self.tmp / 'power-reload-pending'
        for policy in (POLICY, {}):
            with self.subTest(policy=policy):
                failed = self.play(policy, reload_fails=True)
                self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
                self.assertEqual(self.override.exists(), bool(policy))
                self.assertTrue(pending.exists())
                resumed = self.play(policy)
                self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
                self.assertIn('policy reloaded', resumed.stdout)
                self.assertFalse(pending.exists())

    def test_preflight_refuses_unsupported_hibernation_before_any_write(self):
        fakebin = self.tmp / 'bin'
        fakebin.mkdir()
        for version, available, succeeds in [
            ('1.90.9-1', 'yes', False),
            ('1.91.5-1', 'no', False),
            ('1.91.5-1', 'yes', True),
        ]:
            with self.subTest(version=version, available=available):
                for name, output in [('pacman', f'upower {version}'), ('busctl', json.dumps({'data': [available]}))]:
                    script = fakebin / name
                    script.write_text('#!/usr/bin/python3\nprint(' + repr(output) + ')\n')
                    script.chmod(0o755)
                result = self.play(
                    POLICY,
                    tasks=[{'ansible.builtin.import_tasks': str(ROLE / 'tasks/preflight.yml')}],
                    environment={'PATH': str(fakebin) + ':' + os.environ['PATH']},
                )
                self.assertEqual(result.returncode == 0, succeeds, result.stdout + result.stderr)
                self.assertFalse(self.override.exists())


if __name__ == '__main__':
    unittest.main()
