"""System roles read their personal values from the settings and do nothing personal without
them, so a public installation without a supplement applies cleanly."""

import json
from pathlib import Path
import re
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
            if "shaffy['" not in text or role in ('settings', 'operation'):
                continue
            with self.subTest(role=role):
                meta = yaml.safe_load((ROOT / 'roles' / role / 'meta/main.yml').read_text())
                self.assertIn({'role': 'settings'}, meta['dependencies'])

    def test_the_tailnet_role_does_nothing_without_tailnet_settings(self):
        self.assertTrue(ends_role_when(tasks('tailnet')[0], "'tailnet' not in shaffy['settings']"))

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
        gate = next(
            i for i, task in enumerate(steps) if ends_role_when(task, "'kde_connect' not in shaffy['settings']")
        )
        self.assertFalse(any('ufw' in str(task) for task in steps[:gate]))

    def test_localsend_opens_no_port_without_its_settings(self):
        steps = tasks('localsend')
        self.assertTrue(ends_role_when(steps[0], "'localsend' not in shaffy['settings']"))
        self.assertTrue(any('53317' in str(task) for task in steps[1:]))

    def test_the_keyboard_role_installs_only_a_supplement_fragment(self):
        text = (ROOT / 'roles/keyboard/tasks/main.yml').read_text()
        self.assertIn("shaffy['system_overrides']['keyboard_hwdb']", text)
        self.assertFalse(list((ROOT / 'roles/keyboard').glob('files/*')))
        self.assertNotIn('inspect-keyboard-map', text)

    def test_automatic_login_is_opt_in(self):
        steps = tasks('login')
        gate = next(i for i, task in enumerate(steps) if ends_role_when(task, 'not __login_autologin | bool'))
        before = steps[:gate]
        self.assertFalse([t['name'] for t in before if 'ansible.builtin.template' in t or 'ansible.builtin.copy' in t])
        vars_ = yaml.safe_load((ROOT / 'roles/login/vars/main.yml').read_text())
        self.assertIn('default(false)', vars_['__login_autologin'])


class DockerEngineTests(unittest.TestCase):
    def test_published_ports_default_to_loopback_on_every_bridge_network(self):
        daemon = json.loads((ROOT / 'home/dot_config/docker/daemon.json').read_text())
        self.assertEqual(daemon['ip'], '127.0.0.1')
        self.assertEqual(
            daemon['default-network-opts'], {'bridge': {'com.docker.network.bridge.host_binding_ipv4': '127.0.0.1'}}
        )

    def test_the_system_engine_is_retired_without_touching_its_data(self):
        steps = tasks('workflows')
        stop = next(task for task in steps if 'ansible.builtin.systemd_service' in task)
        # A live socket would start the service again, so it stops first.
        self.assertEqual(stop['loop'], ['docker.socket', 'docker.service'])
        self.assertEqual(stop['ansible.builtin.systemd_service']['enabled'], False)
        self.assertEqual(stop['ansible.builtin.systemd_service']['state'], 'stopped')
        # A failed AUR build must leave the old engine running.
        install = next(
            i
            for i, task in enumerate(steps)
            if task.get('ansible.builtin.include_role', {}).get('name') == 'aur_package'
        )
        self.assertLess(install, steps.index(stop))
        # The owner may start the old engine again to rescue data; its configuration keeps the
        # ports it publishes on loopback until the owner deletes it along with the data.
        every_step = str([yaml.safe_load(path.read_text()) for path in (ROOT / 'roles/workflows/tasks').glob('*.yml')])
        for kept in ('/var/lib/docker', '/var/lib/containerd', '/etc/docker'):
            self.assertNotIn(kept, every_step)

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


def _artifacts_on_btrfs():
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    kind = subprocess.run(['stat', '-f', '-c', '%T', ROOT / 'artifacts'], capture_output=True, text=True, check=False)
    return kind.stdout.strip() == 'btrfs'


class DockerDataOrderTests(unittest.TestCase):
    def test_the_subvolume_exists_before_the_launcher_that_starts_the_engine(self):
        # An apply that stopped between the two would leave the engine to make an ordinary directory.
        steps = tasks('workflows')
        subvolume = next(
            i for i, task in enumerate(steps) if task.get('ansible.builtin.import_tasks') == 'docker_data.yml'
        )
        launcher = next(
            i
            for i, task in enumerate(steps)
            if task.get('ansible.builtin.include_role', {}).get('name') == 'aur_package'
        )
        self.assertLess(subvolume, launcher)

    def test_the_subvolume_is_created_as_the_user_whose_engine_writes_to_it(self):
        # The run below cannot switch users without root, so it drops this; a subvolume owned by
        # root would stop the rootless engine at every login.
        steps = yaml.safe_load((ROOT / 'roles/workflows/tasks/docker_data.yml').read_text())
        create = next(step for step in steps if 'ansible.builtin.command' in step)
        self.assertEqual((create['become'], create['become_user']), (True, '{{ shaffy_target_user }}'))


@unittest.skipUnless(_controller_ready() and _artifacts_on_btrfs(), 'needs the mise controller and a Btrfs checkout')
class DockerDataSubvolumeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / 'artifacts')
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.home = self.tmp / 'home'
        self.home.mkdir()
        self.data = self.home / '.local/share/docker'
        # The test already runs as the target user, so the switch to that user would only ask for sudo.
        steps = yaml.safe_load((ROOT / 'roles/workflows/tasks/docker_data.yml').read_text())
        for step in steps:
            step.pop('become', None)
            step.pop('become_user', None)
        (self.tmp / 'docker_data.yml').write_text(yaml.safe_dump(steps))

    def play(self):
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
                            'shaffy_target_home': str(self.home),
                        },
                        'tasks': [{'ansible.builtin.import_tasks': str(self.tmp / 'docker_data.yml')}],
                    }
                ]
            )
        )
        command, env = bootstrap.ansible_command(ROOT, 'ansible-playbook', '-i', 'localhost,', str(play))
        result = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True, timeout=90, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def is_subvolume(self):
        return self.data.stat().st_ino == 256  # the root inode of every Btrfs subvolume

    def test_a_missing_directory_becomes_a_subvolume_once(self):
        self.assertRegex(self.play(), r'changed=1\s')
        self.assertTrue(self.is_subvolume())
        self.assertRegex(self.play(), r'changed=0\s')
        self.assertTrue(self.is_subvolume())

    def test_an_existing_directory_and_its_data_are_left_alone(self):
        self.data.mkdir(parents=True)
        (self.data / 'engine-id').write_text('kept\n')
        self.assertRegex(self.play(), r'changed=0\s')
        self.assertFalse(self.is_subvolume())
        self.assertEqual((self.data / 'engine-id').read_text(), 'kept\n')


# A stand-in for ufw: it keeps its rules in a file beside itself and logs every call, so the
# roles' rules run through community.general.ufw without root or a firewall.
FAKE_UFW = """#!/usr/bin/python3
import json
from pathlib import Path
import sys

here = Path(__file__).parent
arguments = sys.argv[1:]
with open(here / 'calls.log', 'a') as log:
    log.write(json.dumps(arguments) + '\\n')
rules = json.loads((here / 'rules.json').read_text()) if (here / 'rules.json').exists() else {}
if arguments == ['--version']:
    print('ufw 0.36.2')
elif arguments[:1] == ['status']:
    print('Status: active')
    for rule, comment in sorted(rules.items()):
        print(rule, '#', comment)
else:
    dry = arguments[:1] == ['--dry-run']
    arguments = arguments[1:] if dry else arguments
    at = arguments.index('comment')
    rule, comment = ' '.join(arguments[:at]), arguments[at + 1]
    if rule not in rules:
        print('Rule added')
    else:
        print('Skipping adding existing rule' if rules[rule] == comment else 'Rule updated')
    if not dry:
        (here / 'rules.json').write_text(json.dumps(rules | {rule: comment}))
"""


@unittest.skipUnless(_controller_ready(), 'the mise controller is not installed')
class FirewallRuleTests(unittest.TestCase):
    """The KDE Connect and LocalSend openings, through community.general.ufw."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.bin = self.tmp / 'bin'
        self.bin.mkdir()
        (self.bin / 'ufw').write_text(FAKE_UFW)
        (self.bin / 'ufw').chmod(0o755)

    def play(self, role):
        rule = next(task for task in tasks(role) if 'community.general.ufw' in task)
        play = self.tmp / 'play.yml'
        play.write_text(
            yaml.safe_dump(
                [
                    {
                        'hosts': 'localhost',
                        'connection': 'local',
                        'gather_facts': False,
                        'environment': {'PATH': f'{self.bin}:/usr/bin:/bin'},
                        'vars': {
                            'ansible_python_interpreter': '/usr/bin/python3',
                            'shaffy': {'settings': {role: {'interface': 'wlan0', 'subnet': '192.0.2.0/24'}}},
                        },
                        'tasks': [rule],
                    }
                ]
            )
        )
        command, env = bootstrap.ansible_command(ROOT, 'ansible-playbook', '-i', 'localhost,', str(play))
        result = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return re.search(r'changed=(\d+)', result.stdout).group(1)

    def rules(self):
        calls = [json.loads(line) for line in (self.bin / 'calls.log').read_text().splitlines()]
        return [call for call in calls if call != ['--version'] and call[:1] != ['status']]

    def test_each_opening_is_the_same_ufw_rule_as_before_and_converges(self):
        for role, port, comment in (
            ('kde_connect', '1714:1764', 'shaffy KDE Connect'),
            ('localsend', '53317', 'shaffy LocalSend'),
        ):
            with self.subTest(role=role):
                for name in ('calls.log', 'rules.json'):
                    (self.bin / name).unlink(missing_ok=True)
                self.assertEqual(self.play(role), '1')
                self.assertEqual(
                    self.rules(),
                    [
                        [
                            'allow',
                            'in',
                            'on',
                            'wlan0',
                            'from',
                            '192.0.2.0/24',
                            'to',
                            'any',
                            'port',
                            port,
                            'proto',
                            proto,
                            'comment',
                            comment,
                        ]
                        for proto in ('tcp', 'udp')
                    ],
                )
                self.assertEqual(self.play(role), '0')

    def test_a_rule_with_another_comment_is_updated(self):
        self.play('localsend')
        rules = json.loads((self.bin / 'rules.json').read_text())
        (self.bin / 'rules.json').write_text(json.dumps(dict.fromkeys(rules, 'an older comment')))
        self.assertEqual(self.play('localsend'), '1')
        self.assertEqual(set(json.loads((self.bin / 'rules.json').read_text()).values()), {'shaffy LocalSend'})


if __name__ == '__main__':
    unittest.main()
