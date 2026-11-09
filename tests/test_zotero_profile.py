"""Moving an existing Zotero profile to the managed name before the integration adopts it."""

import fcntl
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy.ledger import adoptable

SCRIPT = ROOT / 'scripts/move-zotero-profile.py'
INI = (
    '[Profile0]\nName=default\nIsRelative=1\nPath=abcd1234.default\nDefault=1\n\n'
    '[General]\nStartWithLastProfile=1\nVersion=2\n\n'
)
MANAGED = (ROOT / 'home/private_dot_zotero/private_zotero/profiles.ini').read_text()


class MoveProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home / '.zotero/zotero'
        (self.root / 'abcd1234.default').mkdir(parents=True)
        (self.root / 'abcd1234.default/prefs.js').write_text('user_pref("a", 1);\n')
        (self.root / 'profiles.ini').write_text(INI)

    def move(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPT), '--home', str(self.home), *arguments],
            capture_output=True,
            text=True,
            check=False,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home), 'SHAFFY_TEST_PREREQUISITES': 'fake'},
        )

    def test_without_apply_it_only_prints_the_plan(self):
        result = self.move()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Would rename', result.stdout)
        self.assertEqual((self.root / 'profiles.ini').read_text(), INI)
        self.assertTrue((self.root / 'abcd1234.default').is_dir())

    def test_apply_renames_the_profile_and_matches_the_managed_file(self):
        result = self.move('--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'shaffy.default/prefs.js').read_text(), 'user_pref("a", 1);\n')
        self.assertFalse((self.root / 'abcd1234.default').exists())
        self.assertEqual((self.root / 'profiles.ini').read_text(), MANAGED)
        copies = list((self.home / '.local/state/shaffy/runs').glob('*-zotero-profile/profiles.ini'))
        self.assertEqual([c.read_text() for c in copies], [INI])
        snapshots = (self.home / '.local/state/shaffy/fake-snapshots.jsonl').read_text().splitlines()
        self.assertEqual([json.loads(s)['type'] for s in snapshots], ['pre', 'post'])
        self.assertIn('Snapper pre snapshot 1', result.stderr)
        self.assertIn('Undo, with Zotero closed', result.stdout)
        again = self.move('--apply')
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn('already shaffy.default', again.stdout)

    def test_a_running_zotero_is_refused(self):
        lock = self.root / 'abcd1234.default/.parentlock'
        with open(lock, 'wb') as stream:
            fcntl.lockf(stream, fcntl.LOCK_EX)
            result = self.move('--apply')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Zotero is running', result.stderr)
        self.assertTrue((self.root / 'abcd1234.default').is_dir())
        self.assertEqual(self.move('--apply').returncode, 0)  # the lock left with the process

    def test_an_interrupted_move_is_finished(self):
        (self.root / 'abcd1234.default').rename(self.root / 'shaffy.default')
        result = self.move('--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'profiles.ini').read_text(), MANAGED)

    def test_ambiguous_profiles_are_left_to_the_user(self):
        (self.root / 'profiles.ini').write_text(INI + '[Profile1]\nName=b\nIsRelative=1\nPath=b.default\n')
        self.assertIn('lists 2 profiles', self.move('--apply').stderr)
        (self.root / 'profiles.ini').write_text(INI)
        (self.root / 'shaffy.default').mkdir()
        self.assertIn('already exists', self.move('--apply').stderr)

    def test_without_a_snapshot_nothing_moves(self):
        # The test adapter refuses the account's real home, so no snapshot is possible here;
        # the real snapper is never used by a test.
        result = subprocess.run(
            [sys.executable, str(SCRIPT), '--home', str(self.home), '--apply'],
            capture_output=True,
            text=True,
            check=False,
            env={'PATH': '/usr/bin:/bin', 'SHAFFY_TEST_PREREQUISITES': 'fake'},
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn('no snapshot can be taken first, so nothing was changed', result.stderr)
        self.assertTrue((self.root / 'abcd1234.default').is_dir())
        self.assertEqual((self.root / 'profiles.ini').read_text(), INI)

    def test_adopting_requires_the_managed_profile(self):
        from shaffy.integrations import load_catalogue
        from shaffy.loader import parse

        zotero = load_catalogue(ROOT, parse)[0]['zotero']
        self.assertIn('scripts/move-zotero-profile.py --apply', adoptable(zotero, self.home))
        self.assertEqual(self.move('--apply').returncode, 0)
        self.assertIsNone(adoptable(zotero, self.home))


if __name__ == '__main__':
    unittest.main()
