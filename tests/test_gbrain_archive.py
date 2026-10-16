"""Recovery integrity and cooperative database exclusion before retirement."""
import fcntl
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = runpy.run_path(str(ROOT / 'scripts/archive-gbrain.py'))


class GBrainArchiveTests(unittest.TestCase):
    def test_verification_detects_changed_missing_and_added_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('raw', 'export'):
                (root / name).mkdir()
                (root / name / 'data').write_bytes(b'original')
                (root / name / 'link').symlink_to('data')
            manifest = {name: ARCHIVE['inventory'](root / name) for name in ('raw', 'export')}
            (root / 'manifest.json').write_text(json.dumps(manifest))
            ARCHIVE['verify'](root)
            (root / 'raw/data').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'raw archive verification'):
                ARCHIVE['verify'](root)
            (root / 'raw/data').write_bytes(b'original')
            (root / 'export/extra').write_bytes(b'unexpected')
            with self.assertRaisesRegex(ValueError, 'export archive verification'):
                ARCHIVE['verify'](root)

    def test_database_lock_refuses_active_owner_and_releases_after_error(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'db'
            lock = Path(str(database) + '.gbrain-owner.lock')
            with lock.open('a') as owner:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError, 'still owns'):
                    with ARCHIVE['exclusive_database'](database):
                        self.fail('Acquired an occupied lock')
            with self.assertRaises(RuntimeError):
                with ARCHIVE['exclusive_database'](database):
                    raise RuntimeError('copy failed')
            with ARCHIVE['exclusive_database'](database):
                pass

    def test_active_service_is_restored_when_snapshot_fails(self):
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as mocked:
            with self.assertRaises(RuntimeError):
                with ARCHIVE['paused_service']():
                    raise RuntimeError('copy failed')
            self.assertEqual([call.args[0][-2:] for call in mocked.call_args_list],
                             [['is-active', 'gbrain-mcp.service'], ['stop', 'gbrain-mcp.service'],
                              ['start', 'gbrain-mcp.service']])

    def test_unknown_service_state_fails_without_copy_or_stop(self):
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as mocked:
            with self.assertRaisesRegex(ValueError, 'service state'):
                with ARCHIVE['paused_service']():
                    self.fail('Snapshot started without service state')
            self.assertEqual(mocked.call_count, 1)


if __name__ == '__main__':
    unittest.main()
