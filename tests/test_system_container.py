"""The system route as root in a disposable CachyOS container.

Opt in with SHAFFY_CONTAINER_TESTS=1: it needs Docker, the recorded image and
network access for the bootstrap, and takes minutes. tests/container/system.sh
does the work; this test reads its RESULT lines.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
IMAGE = json.loads((ROOT / 'tests/tool-versions.json').read_text())['ci_container']
FAULTS = (
    'after-pending',
    'after-root-pre-snapshots',
    'after-root-pending',
    'after-root-phase-start',
    'after-root-phase-done',
    'after-play',
)


def _unavailable():
    if os.environ.get('SHAFFY_CONTAINER_TESTS') != '1':
        return 'set SHAFFY_CONTAINER_TESTS=1 to run the container tier'
    if shutil.which('docker') is None:
        return 'docker is not installed'
    return ''


@unittest.skipIf(_unavailable(), _unavailable())
class SystemContainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            ['docker', 'run', '--rm', '-v', f'{ROOT}:/src:ro', IMAGE, 'bash', '/src/tests/container/system.sh'],
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
        cls.output = result.stdout + result.stderr
        cls.code = result.returncode
        cls.results = {}
        for line in result.stdout.splitlines():
            if line.startswith('RESULT '):
                name, _, rest = line[len('RESULT ') :].partition(' ')
                cls.results[name] = rest

    def state(self, name):
        return json.loads(re.search(r'(\{.*\})$', self.results[name]).group(1))

    def test_the_script_completes(self):
        self.assertEqual(self.code, 0, self.output[-4000:])

    def test_the_bootstrap_leaves_the_system_scope_ready(self):
        self.assertIn('System scope: prerequisites are in place.', self.results['check'])

    def test_two_applies_converge_and_bind_the_system(self):
        for attempt in ('first', 'second'):
            self.assertIn('code=0', self.results[f'apply-{attempt}'])
        self.assertNotIn('changed=0', self.results['apply-first'])  # the extra package was installed
        self.assertIn('changed=0', self.results['apply-second'])
        state = self.state('state-second')
        self.assertTrue(state['bound'])
        self.assertIsNone(state['user_pending'])
        self.assertIsNone(state['root_pending'])
        self.assertEqual(state['root_last'], state['user_last'])

    def test_the_desktop_prerequisites_and_groups_are_installed(self):
        self.assertEqual(self.results['desktop'].split(), ['grim', 'tesseract', 'localsend', 'restic', 'htop'])

    def test_a_jinja_shaped_setting_stays_literal_through_the_play(self):
        self.assertEqual(self.results['jinja-first'], 'literal')
        self.assertEqual(self.results['jinja-second'], 'literal')

    def test_every_crash_between_record_writes_recovers(self):
        for fault in FAULTS:
            with self.subTest(fault=fault):
                self.assertNotIn('code=0', self.results[f'crash-{fault}'].split(' {')[0])
                crashed = self.state(f'crash-{fault}')
                self.assertIsNotNone(crashed['user_pending'])
                self.assertIn('code=0', self.results[f'resume-{fault}'])
                resumed = self.state(f'resume-{fault}')
                self.assertIsNone(resumed['user_pending'])
                self.assertIsNone(resumed['root_pending'])

    def test_a_crash_before_root_is_cleared_and_a_later_one_completes(self):
        for fault in FAULTS[:2]:  # root never held these operations
            with self.subTest(fault=fault):
                self.assertNotEqual(
                    self.state(f'resume-{fault}')['user_last'], self.state(f'crash-{fault}')['user_pending']
                )
        for fault in FAULTS[2:]:
            with self.subTest(fault=fault):
                crashed, resumed = self.state(f'crash-{fault}'), self.state(f'resume-{fault}')
                self.assertEqual(resumed['user_last'], crashed['user_pending'])
                self.assertEqual(resumed['root_last'], crashed['user_pending'])

    def test_settings_that_always_fail_can_be_abandoned(self):
        failing = self.results['failing']
        for step in ('apply=1', 'resume=1', 'abandon=0'):
            self.assertIn(step, failing)
        state = self.state('failing')
        self.assertIsNone(state['user_pending'])
        self.assertIsNone(state['root_pending'])
        self.assertEqual(self.results['group-refusal'], 'named')
        self.assertIn('code=0', self.results['after-abandon'])

    def test_a_root_operation_left_by_a_rollback_is_abandoned_explicitly(self):
        rollback = self.results['rollback']
        for step in ('refused=1', 'abandon=0', 'apply=0'):
            self.assertIn(step, rollback)
        self.assertIsNone(self.state('rollback')['root_pending'])

    def test_an_aur_package_is_built_as_the_user_and_installed_from_a_verified_copy(self):
        self.assertIn('failed=0', self.results['aur-first'])
        self.assertNotIn('changed=0', self.results['aur-first'])
        self.assertIn('changed=0', self.results['aur-second'])  # installed: left to yay
        installed = self.results['aur-installed']
        self.assertIn('shaffy-probe 1.0-1', installed)
        self.assertIn('copy=shaffy-probe-1.0-1-any.pkg.tar', installed)
        self.assertIn('shaffy-probe.json', installed)  # the digest recorded beside root's copy
        self.assertIn('build=shaffy-probe-1.0-1-any.pkg.tar', installed)


if __name__ == '__main__':
    unittest.main()
