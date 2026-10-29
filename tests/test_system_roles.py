"""System roles read their personal values from the settings and do nothing personal without
them, so a public installation without a supplement applies cleanly."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy import bootstrap


def tasks(role):
    return yaml.safe_load((ROOT / 'roles' / role / 'tasks/main.yml').read_text())


def ends_role_when(task, condition):
    return task.get('ansible.builtin.meta') == 'end_role' and task.get('when') == condition


class SettingsGatedRoleTests(unittest.TestCase):
    def test_every_role_reading_the_settings_depends_on_the_settings_role(self):
        for role in sorted(p.name for p in (ROOT / 'roles').iterdir() if (p / 'tasks').is_dir()):
            text = ''.join(p.read_text() for p in (ROOT / 'roles' / role).rglob('*.yml'))
            if 'shaffy.' not in text or role in ('settings', 'operation'):
                continue
            with self.subTest(role=role):
                meta = yaml.safe_load((ROOT / 'roles' / role / 'meta/main.yml').read_text())
                self.assertIn({'role': 'settings'}, meta['dependencies'])

    def test_the_tailnet_role_does_nothing_without_tailnet_settings(self):
        self.assertTrue(ends_role_when(tasks('tailnet')[0], "'tailnet' not in shaffy.settings"))

    def test_directly_connected_networks_stay_direct_before_accepted_subnet_routes(self):
        unit = (ROOT / 'roles/tailnet/files/shaffy-tailnet-local-networks.service').read_text()
        add = next(line for line in unit.splitlines() if line.startswith('ExecStart='))
        self.assertIn('lookup main suppress_prefixlength 0', add)
        priority = int(add.split('priority ')[1].split()[0])
        # Before Tailscale's table 52 at 5270, and none of the priorities Tailscale deletes as its own.
        self.assertLess(priority, 5270)
        self.assertNotIn(priority, (5210, 5230, 5250))
        steps = tasks('tailnet')
        enable = next(
            i
            for i, task in enumerate(steps)
            if task.get('ansible.builtin.systemd_service', {}).get('name') == 'shaffy-tailnet-local-networks.service'
        )
        accept = next(i for i, task in enumerate(steps) if '--accept-routes=true' in str(task))
        self.assertLess(enable, accept)

    def test_kde_connect_opens_no_port_without_its_settings(self):
        steps = tasks('kde_connect')
        gate = next(i for i, task in enumerate(steps) if ends_role_when(task, "'kde_connect' not in shaffy.settings"))
        self.assertFalse(any('ufw' in str(task) for task in steps[:gate]))

    def test_localsend_opens_no_port_without_its_settings(self):
        steps = tasks('localsend')
        self.assertTrue(ends_role_when(steps[0], "'localsend' not in shaffy.settings"))
        self.assertTrue(any('53317' in str(task) for task in steps[1:]))

    def test_the_keyboard_role_installs_only_a_supplement_fragment(self):
        text = (ROOT / 'roles/keyboard/tasks/main.yml').read_text()
        self.assertIn('shaffy.system_overrides.keyboard_hwdb', text)
        self.assertFalse(list((ROOT / 'roles/keyboard').glob('files/*')))
        self.assertNotIn('inspect-keyboard-map', text)

    def test_automatic_login_is_opt_in(self):
        steps = tasks('login')
        gate = next(i for i, task in enumerate(steps) if ends_role_when(task, 'not login_autologin'))
        before = steps[:gate]
        self.assertFalse([t['name'] for t in before if 'ansible.builtin.template' in t or 'ansible.builtin.copy' in t])
        vars_ = yaml.safe_load((ROOT / 'roles/login/vars/main.yml').read_text())
        self.assertIn('default(false)', vars_['login_autologin'])


class DockerEngineTests(unittest.TestCase):
    def test_published_ports_default_to_loopback_on_every_bridge_network(self):
        daemon = json.loads((ROOT / 'roles/workflows/files/daemon.json').read_text())
        self.assertEqual(daemon['ip'], '127.0.0.1')
        self.assertEqual(
            daemon['default-network-opts'], {'bridge': {'com.docker.network.bridge.host_binding_ipv4': '127.0.0.1'}}
        )

    def test_the_engine_starts_with_its_configuration_and_restarts_when_it_changes(self):
        steps = tasks('workflows')
        config = next(
            i
            for i, task in enumerate(steps)
            if task.get('ansible.builtin.copy', {}).get('dest') == '/etc/docker/daemon.json'
        )
        start = next(
            i
            for i, task in enumerate(steps)
            if task.get('ansible.builtin.systemd_service', {}).get('name') == 'docker.service'
        )
        self.assertLess(config, start)
        self.assertEqual(steps[config]['notify'], 'Restart the Docker engine')
        self.assertEqual(steps[-1].get('ansible.builtin.meta'), 'flush_handlers')
        handlers = yaml.safe_load((ROOT / 'roles/workflows/handlers/main.yml').read_text())
        self.assertIn('Restart the Docker engine', [handler['name'] for handler in handlers])

    def test_no_role_adds_a_user_to_the_root_equivalent_docker_group(self):
        def user_tasks(node):  # tasks nested in blocks included
            if isinstance(node, dict):
                for key in ('ansible.builtin.user', 'user'):
                    if isinstance(node.get(key), dict):
                        yield node[key]
                for value in node.values():
                    yield from user_tasks(value)
            elif isinstance(node, list):
                for item in node:
                    yield from user_tasks(item)

        for path in sorted((ROOT / 'roles').glob('*/tasks/*.yml')):
            for user in user_tasks(yaml.safe_load(path.read_text())):
                groups = user.get('groups', [])
                groups = groups.split(',') if isinstance(groups, str) else groups
                self.assertNotIn('docker', [group.strip() for group in groups], path)


if __name__ == '__main__':
    unittest.main()


def _controller_ready():
    return (
        subprocess.run(['mise', 'where', 'ansible-core'], cwd=ROOT, capture_output=True, check=False).returncode == 0
        if bootstrap.shutil.which('mise')
        else False
    )


@unittest.skipUnless(_controller_ready(), 'the mise controller is not installed')
class PackagesEvaluationTests(unittest.TestCase):
    """The packages role's expressions evaluate with the public defaults' empty lists: its group
    check once read the output of a skipped lookup, and the first system apply of a VM failed."""

    def test_empty_package_settings_apply_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / 'settings.json'
            settings.write_text(json.dumps({'shaffy': {'packages': {'extra': [], 'remove': [], 'omit': []}}}))
            play = Path(directory) / 'play.yml'
            play.write_text(
                '- hosts: localhost\n  connection: local\n  gather_facts: false\n  roles:\n    - packages\n'
            )
            command, env = bootstrap.ansible_command(
                ROOT,
                'ansible-playbook',
                '-i',
                'localhost,',
                '--check',
                str(play),
                '-e',
                f'shaffy_settings_file={settings}',
            )
            env['ANSIBLE_ROLES_PATH'] = str(ROOT / 'roles')
            result = subprocess.run(
                command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300, check=False
            )
        self.assertEqual(result.returncode, 0, result.stdout[-2000:])
        self.assertIn('failed=0', result.stdout)
