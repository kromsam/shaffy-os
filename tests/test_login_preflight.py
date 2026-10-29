"""Login prerequisites are all checked before the first write."""

import getpass
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shaffy import bootstrap
from support import host_test

TASKS = ROOT / 'roles/login/tasks'
READ_ONLY = {
    'ansible.builtin.stat',
    'ansible.builtin.slurp',
    'ansible.builtin.assert',
    'ansible.builtin.getent',
    'ansible.builtin.command',
}  # commands only with changed_when: false (checked below)


def modules(task):
    return {key for key in task if key.startswith('ansible.builtin.')}


class LoginPreflightTests(unittest.TestCase):
    def test_the_role_starts_with_the_preflight(self):
        first = yaml.safe_load((TASKS / 'main.yml').read_text())[0]
        self.assertEqual(first.get('ansible.builtin.import_tasks'), 'preflight.yml')

    def test_the_preflight_only_reads(self):
        for task in yaml.safe_load((TASKS / 'preflight.yml').read_text()):
            with self.subTest(task=task['name']):
                self.assertLessEqual(modules(task), READ_ONLY)
                if 'ansible.builtin.command' in task:
                    self.assertIs(task.get('changed_when'), False)

    def test_no_check_runs_after_a_write(self):
        for name in ('main.yml', 'keyring.yml'):
            for task in yaml.safe_load((TASKS / name).read_text()):
                with self.subTest(file=name, task=task['name']):
                    self.assertFalse(modules(task) & {'ansible.builtin.assert', 'ansible.builtin.stat'})


def _controller_ready():
    return (
        subprocess.run(['mise', 'where', 'ansible-core'], cwd=ROOT, capture_output=True, check=False).returncode == 0
        if bootstrap.shutil.which('mise')
        else False
    )


@unittest.skipUnless(_controller_ready(), 'the mise controller is not installed')
class PreflightEvaluationTests(unittest.TestCase):
    """The preflight's expressions evaluate: structure alone once hid a filter that always failed."""

    def preflight(self, user, home=None, autologin=True):
        with tempfile.TemporaryDirectory() as directory:
            # The settings an operation would pass: automatic login requested.
            settings = Path(directory) / 'settings.json'
            settings.write_text(json.dumps({'shaffy': {'settings': {'login': {'autologin': autologin}}}}))
            play = Path(directory) / 'play.yml'
            play.write_text(
                '- hosts: localhost\n  connection: local\n  gather_facts: false\n  tasks:\n'
                '    - ansible.builtin.include_role:\n        name: login\n        tasks_from: preflight\n'
            )
            command, env = bootstrap.ansible_command(
                ROOT,
                'ansible-playbook',
                '-i',
                'localhost,',
                str(play),
                '-e',
                f'shaffy_target_user={user}',
                '-e',
                f'shaffy_target_home={home or Path.home()}',
                '-e',
                f'shaffy_settings_file={settings}',
            )
            env['ANSIBLE_ROLES_PATH'] = str(ROOT / 'roles')
            return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300, check=False)

    @host_test("reads this laptop's Niri session and PAM stack")
    def test_this_laptop_passes_for_its_user(self):
        result = self.preflight(getpass.getuser())
        self.assertEqual(result.returncode, 0, result.stdout[-2000:])

    @host_test("reads this laptop's Niri session and PAM stack")
    def test_another_user_is_refused_before_any_write(self):
        result = self.preflight('someone-else')  # no such account
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('There is no account named someone-else', result.stdout)
        self.assertNotIn('Unknown argument', result.stdout)

    @host_test("reads this laptop's Niri session and PAM stack")
    def test_a_default_keyring_under_another_name_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as home:
            keyrings = Path(home) / '.local/share/keyrings'
            keyrings.mkdir(parents=True)
            (keyrings / 'default').write_text('Standaard_sleutelbos')
            (keyrings / 'Standaard_sleutelbos.keyring').write_bytes(b'GnomeKeyring\n')
            result = self.preflight(getpass.getuser(), home)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('is not the login keyring', result.stdout)

    @host_test("reads this laptop's Niri session and PAM stack")
    def test_an_account_without_keyrings_passes(self):
        with tempfile.TemporaryDirectory() as home:
            result = self.preflight(getpass.getuser(), home)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:])

    def test_without_automatic_login_nothing_is_checked(self):
        result = self.preflight('someone-else', autologin=False)  # would be refused if checked
        self.assertEqual(result.returncode, 0, result.stdout[-2000:])


if __name__ == '__main__':
    unittest.main()
