"""The system bootstrap uses mise and pinned collections, and reports its gaps."""

import configparser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shaffy import bootstrap


class VersionRecordTests(unittest.TestCase):
    def test_mise_declares_only_the_latest_ansible_core(self):
        # The checkout's mise.toml is the controller; personal and desktop tools belong in the
        # home's mise configuration, or `mise install` in any checkout would fetch them too.
        tools = tomllib.loads((ROOT / 'mise.toml').read_text())['tools']
        self.assertEqual(tools, {'ansible-core': 'latest'})

    def test_collections_are_pinned_to_exact_versions(self):
        pinned = bootstrap.pinned_collections(ROOT)
        self.assertIn('community.general', pinned)
        for name, version in pinned.items():
            self.assertRegex(version, r'^\d+\.\d+\.\d+$', name)

    def test_ansible_reads_only_the_checkout_collections(self):
        config = configparser.ConfigParser()
        config.read(ROOT / 'ansible.cfg')
        self.assertEqual(config['defaults']['collections_path'], './collections')
        self.assertEqual(config['defaults']['collections_scan_sys_path'], 'False')

    @unittest.skipUnless(shutil.which('git') and (ROOT / '.git').exists(), 'needs a git checkout')
    def test_installed_collections_stay_out_of_git(self):
        ignored = subprocess.run(
            [
                'git',
                '-C',
                str(ROOT),
                'check-ignore',
                '-q',
                'collections/ansible_collections/community/general/MANIFEST.json',
            ],
            check=False,
        )
        self.assertEqual(ignored.returncode, 0)

    def test_no_package_list_may_remove_the_bootstrap(self):
        from shaffy.packages import PROTECTED

        self.assertLessEqual(set(bootstrap.PACKAGES), PROTECTED)

    def test_the_readme_documents_the_same_step(self):
        readme = (ROOT / 'README.md').read_text()
        self.assertIn(bootstrap.pacman_step(), readme)
        self.assertIn(bootstrap.galaxy_step(), readme)


class ReportTests(unittest.TestCase):
    def test_check_without_chezmoi_prints_the_install_step(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(ROOT / 'bin/shaffy'), 'check', '--no-render', '--without-supplement'],
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, 'HOME': directory, 'XDG_STATE_HOME': directory, 'PATH': ''},
            )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('missing packages: chezmoi', result.stderr)
        self.assertIn(bootstrap.pacman_step(), result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_missing_commands_and_modules_name_the_pacman_step(self):
        with (
            mock.patch.object(bootstrap.shutil, 'which', return_value=None),
            mock.patch.object(bootstrap.importlib.util, 'find_spec', return_value=None),
        ):
            problems = bootstrap._packages()
        self.assertEqual(len(problems), 1)
        self.assertIn(bootstrap.pacman_step(), problems[0])
        # Every package of the documented step is checked by a command or a module.
        checked = set(bootstrap.COMMANDS.values()) | set(bootstrap.MODULES.values())
        self.assertEqual(checked, set(bootstrap.PACKAGES))

    def test_collection_versions_are_compared_with_the_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            (checkout / 'requirements.yml').write_text(
                'collections:\n  - name: community.general\n    version: 13.4.0\n'
            )
            self.assertIn('community.general 13.4.0', bootstrap._collections(checkout)[0])
            manifest = checkout / 'collections/ansible_collections/community/general/MANIFEST.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({'collection_info': {'version': '12.0.0'}}))
            self.assertIn('(found 12.0.0)', bootstrap._collections(checkout)[0])
            manifest.write_text(json.dumps({'collection_info': {'version': '13.4.0'}}))
            self.assertEqual(bootstrap._collections(checkout), [])

    def test_mise_messages_become_the_command_to_run(self):
        def mise(stderr, code=1):
            return subprocess.CompletedProcess([], code, '', stderr)

        untrusted = 'mise ERROR Config files in /x/mise.toml are not trusted.\nTrust them with `mise trust`.'
        with mock.patch.object(bootstrap.subprocess, 'run', return_value=mise(untrusted)):
            self.assertIn('mise trust /x/mise.toml', bootstrap._controller(ROOT)[0])
        with mock.patch.object(
            bootstrap.subprocess, 'run', return_value=mise('mise ERROR ansible-core@latest not installed')
        ):
            self.assertIn('mise install ansible-core', bootstrap._controller(ROOT)[0])
        with mock.patch.object(bootstrap.subprocess, 'run', return_value=mise('', 0)):
            self.assertEqual(bootstrap._controller(ROOT), [])

    def test_the_controller_runs_only_the_mise_managed_tool(self):
        command, env = bootstrap.ansible_command(ROOT, 'ansible-playbook', '--version')
        self.assertEqual(command[:4], ['mise', 'exec', 'ansible-core', '--'])
        self.assertEqual(env['ANSIBLE_CONFIG'], str(ROOT / 'ansible.cfg'))

    def test_inherited_ansible_settings_are_dropped(self):
        with mock.patch.dict(
            bootstrap.os.environ, {'ANSIBLE_SKIP_TAGS': 'always', 'ANSIBLE_COLLECTIONS_PATH': '/elsewhere'}
        ):
            _, env = bootstrap.ansible_command(ROOT, 'ansible-playbook')
        self.assertEqual([key for key in env if key.startswith('ANSIBLE_')], ['ANSIBLE_CONFIG'])


def _controller_ready():
    if shutil.which('mise') is None:
        return False
    return subprocess.run(
        ['mise', 'where', 'ansible-core'], cwd=ROOT, capture_output=True, check=False
    ).returncode == 0 and not bootstrap._collections(ROOT)


@unittest.skipUnless(_controller_ready(), 'the mise controller and pinned collections are not installed')
class MiseControllerTests(unittest.TestCase):
    def test_every_playbook_passes_the_syntax_check(self):
        for playbook in sorted(ROOT.glob('*.yml')):
            if playbook.name == 'requirements.yml':
                continue
            with self.subTest(playbook=playbook.name):
                command, env = bootstrap.ansible_command(
                    ROOT, 'ansible-playbook', '-i', 'localhost,', '--syntax-check', str(playbook)
                )
                result = subprocess.run(
                    command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=False
                )
                self.assertEqual(result.returncode, 0, result.stderr[-1500:])

    def test_the_controller_sees_only_the_pinned_collections(self):
        command, env = bootstrap.ansible_command(ROOT, 'ansible-galaxy', 'collection', 'list', '--format', 'json')
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        found = {name: info['version'] for path in json.loads(result.stdout).values() for name, info in path.items()}
        self.assertEqual(found, bootstrap.pinned_collections(ROOT))


@unittest.skipUnless(_controller_ready(), 'the mise controller and pinned collections are not installed')
class SettingsRouteTests(unittest.TestCase):
    """The play reads the loader's JSON with slurp and the operation from SHAFFY_SYSTEM."""

    def play(self, settings, tasks):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / 'play.yml').write_text(
                '- hosts: localhost\n  connection: local\n  gather_facts: false\n'
                '  roles:\n    - role: settings\n  tasks:\n' + tasks
            )
            arguments = ['ansible-playbook', '-i', 'localhost,', str(directory / 'play.yml')]
            if settings is not None:
                (directory / 'settings.json').write_text(json.dumps(settings))
                arguments += ['-e', f'shaffy_settings_file={directory / "settings.json"}']
            command, env = bootstrap.ansible_command(ROOT, *arguments)
            env['ANSIBLE_ROLES_PATH'] = str(ROOT / 'roles')
            return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300, check=False)

    def settings(self):
        result = subprocess.run(
            [str(ROOT / 'bin/shaffy'), 'data', '--without-supplement'], capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout)

    def test_jinja_shaped_values_stay_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'ran'
            settings = self.settings()
            settings['shaffy']['settings']['identity'] = {'name': "{{ lookup('pipe', 'touch " + str(marker) + "') }}"}
            result = self.play(
                settings,
                '    - ansible.builtin.set_fact:\n'
                '        copied: "{{ shaffy.settings.identity.name }}"\n'
                '    - ansible.builtin.debug:\n        msg: "{{ copied }}"\n',
            )
            self.assertEqual(result.returncode, 0, result.stdout[-1500:])
            self.assertIn("lookup('pipe'", result.stdout)
            self.assertFalse(marker.exists(), 'a settings value was evaluated as Jinja')

    def test_the_operation_from_the_environment_stays_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'play.yml').write_text(
                '- hosts: localhost\n  connection: local\n  gather_facts: false\n  vars:\n'
                "    shaffy_system: \"{{ lookup('ansible.builtin.env', 'SHAFFY_SYSTEM', default='{}') | from_json }}\"\n"
                '  tasks:\n    - ansible.builtin.debug:\n        msg: "capture={{ shaffy_system.capture }}"\n'
            )
            command, env = bootstrap.ansible_command(
                ROOT, 'ansible-playbook', '-i', 'localhost,', str(Path(directory) / 'play.yml')
            )
            env['SHAFFY_SYSTEM'] = json.dumps({'capture': '/home/x/st{{6*7}}/capture'})
            result = subprocess.run(
                command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300, check=False
            )
        self.assertEqual(result.returncode, 0, result.stdout[-1500:])
        self.assertIn('capture=/home/x/st{{6*7}}/capture', result.stdout)

    def test_the_package_groups_reach_the_pacman_module(self):
        result = self.play(
            self.settings(),
            '    - community.general.pacman:\n'
            '        name: "{{ shaffy.packages.groups.sync }}"\n'
            '        state: present\n      check_mode: true\n',
        )
        self.assertEqual(result.returncode, 0, result.stdout[-1500:])

    def test_a_play_without_the_settings_file_refuses(self):
        result = self.play(None, '    - ansible.builtin.debug:\n        msg: unreachable\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('./bin/shaffy apply --system', result.stdout)


if __name__ == '__main__':
    unittest.main()
