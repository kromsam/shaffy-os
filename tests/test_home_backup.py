"""Exercise backup scope, retention isolation and real Restic restore verification."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import host_test  # noqa: E402

PATH = Path(__file__).parents[1] / 'home/private_dot_local/bin/executable_restic-home'
loader = importlib.machinery.SourceFileLoader('home_backup', str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)

SETTINGS = {'home': '/home/tester', 'host': 'laptop', 'tags': ['automatic', 'shaffy'],
            'repository': 'sftp:restic-home-nas:repository', 'repository_id': None,
            'retention': {'daily': 14, 'weekly': 8, 'monthly': 12}}


class BackupScope(unittest.TestCase):
    def test_other_hosts_paths_and_tags_are_rejected(self):
        valid = {'hostname': 'laptop', 'paths': ['/home/tester'], 'tags': ['automatic', 'shaffy']}
        module.verify_snapshot(valid, SETTINGS)
        for override in [{'hostname': 'another-host'}, {'paths': ['/home/old']},
                         {'paths': ['/home/tester', '/etc']}, {'tags': ['automatic']}, {'tags': None}]:
            with self.subTest(override=override), self.assertRaises(RuntimeError):
                module.verify_snapshot({**valid, **override}, SETTINGS)

    def test_retention_is_scoped_and_follows_the_settings(self):
        arguments = module.retention_args({**SETTINGS, 'retention': {'daily': 7, 'weekly': 4, 'monthly': 6}})
        self.assertEqual(arguments[:8], ['forget', '--host', 'laptop', '--path', '/home/tester', '--tag',
                                         'automatic,shaffy', '--group-by'])
        self.assertEqual(arguments[-6:], ['--keep-daily', '7', '--keep-weekly', '4', '--keep-monthly', '6'])

    def test_missing_or_failed_backup_has_no_success_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'log'
            path.write_text('{"message_type":"status"}\n')
            with self.assertRaises(RuntimeError):
                module.parse_backup_summary(path)

    def test_snapshot_times_compare_with_the_file_system_in_microseconds(self):
        self.assertEqual(module.mtime_us('2026-09-24T06:14:52.705913113+02:00'), 1790223292705913)
        self.assertEqual(module.mtime_us('2026-09-24T04:14:52Z'), 1790223292000000)

    def test_the_receipt_is_read_from_the_state_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config = home / '.config/restic-home'
            config.mkdir(parents=True)
            for name in ['password', 'id_ed25519', 'ssh_config', 'known_hosts', 'excludes']:
                (config / name).write_text('fixture\n')
            backup = module.Backup(home, {**SETTINGS, 'home': str(home)})
            with self.assertRaisesRegex(RuntimeError, 'no verified backup yet'):
                backup.last_receipt()
            (backup.state / 'last-success.json').write_text('{"snapshot_id": "own"}')
            self.assertEqual(backup.last_receipt()['snapshot_id'], 'own')

    def test_settings_rendered_for_another_home_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / '.config/restic-home').mkdir(parents=True)
            (home / '.config/restic-home/backup.json').write_text(json.dumps(SETTINGS))
            with self.assertRaises(RuntimeError):
                module.load_settings(home)


@host_test('a real Restic binary', paths=('/usr/bin/restic',))
class RealRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.home = root / 'home'
        self.config = self.home / '.config/restic-home'
        self.config.mkdir(parents=True)
        for name in ['password', 'id_ed25519', 'ssh_config', 'known_hosts']:
            (self.config / name).write_text('fixture\n')
        (self.config / 'excludes').write_text('$HOME/.cache\n$HOME/Projects/database\n')
        for relative, data in (('Documenten/notes.md', b'# notes\n'), ('Documenten/brief.odt', bytes(range(256)) * 40),
                               ('Projects/site/index.html', b'<p>hi</p>\n'),
                               ('Projects/database/table.db', b'never sampled\n'),
                               ('.ssh/id_ed25519', b'never sampled either\n')):
            path = self.home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.settings = {**SETTINGS, 'home': str(self.home), 'repository': str(root / 'repository')}
        self.backup = self.client()
        self.backup.run('init', stdout=subprocess.DEVNULL)

    def client(self, **changes):
        backup = module.Backup(self.home, {**self.settings, **changes})
        backup.env.pop('RESTIC_PASSWORD_FILE')
        backup.env['RESTIC_PASSWORD'] = 'fixture-password'
        backup.command = ['/usr/bin/restic']
        return backup

    def test_an_unpinned_repository_only_allows_inspection(self):
        repo_id = self.backup.repository(allow_unpinned=True)
        self.assertRegex(repo_id, '^[0-9a-f]{64}$')
        with self.assertRaisesRegex(RuntimeError, 'no repository ID yet'):
            self.backup.repository()
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            self.client(repository_id='0' * 64).repository()
        # inspect shows the repository it finds even when the settings pin another one.
        self.assertEqual(self.client(repository_id='0' * 64).repository(allow_unpinned=True), repo_id)
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            self.backup.init()

    def test_backup_is_verified_by_marker_and_random_unchanged_files(self):
        backup = self.client(repository_id=self.backup.repository(allow_unpinned=True))
        # An older snapshot of another host must never enter this home's retention.
        backup.run('backup', '--host', 'old-laptop', '--tag', 'automatic', str(self.home), stdout=subprocess.DEVNULL)
        backup.backup()
        receipt = json.loads((backup.state / 'last-success.json').read_text())
        self.assertEqual(receipt['status'], 'verified')
        marker, *randoms = receipt['samples']
        self.assertEqual(marker['path'], str(backup.state / 'marker'))
        self.assertGreaterEqual(receipt['random_samples'], 1)
        self.assertEqual(receipt['verified_samples'], 1 + len(randoms))
        for sample in randoms:  # never hidden or excluded paths, whose hashes would leak or mislead
            relative = Path(sample['path']).relative_to(self.home)
            self.assertFalse(any(part.startswith('.') for part in relative.parts), relative)
            self.assertNotEqual(relative.parts[:2], ('Projects', 'database'))
        self.assertEqual(len(backup.snapshots()), 1)
        self.assertEqual(len(backup.json('snapshots', '--json')), 2)
        backup.retention()
        result = backup.json(*module.retention_args(backup.settings), '--dry-run', '--json')
        ids = {s['id'] for group in result for key in ('keep', 'remove') for s in (group.get(key) or [])}
        self.assertEqual(ids, {receipt['snapshot_id']})
        self.assertEqual(backup.verify(receipt['snapshot_id'], receipt['samples']), len(receipt['samples']))
        # Corrupted expected samples fail, rather than merely trusting a saved receipt.
        receipt['samples'][0]['sha256'] = '0' * 64
        with self.assertRaises(RuntimeError):
            backup.verify(receipt['snapshot_id'], receipt['samples'])

    def test_a_file_changed_after_the_backup_is_not_sampled(self):
        backup = self.client(repository_id=self.backup.repository(allow_unpinned=True))
        backup.backup()
        snapshot = json.loads((backup.state / 'last-success.json').read_text())['snapshot_id']
        for path in (self.home / 'Documenten/notes.md', self.home / 'Documenten/brief.odt',
                     self.home / 'Projects/site/index.html'):
            path.write_bytes(b'changed\n')
            os.utime(path, ns=(1, 1))  # older than the run, but no longer what the snapshot holds
        self.assertEqual(backup.random_samples(snapshot, 10**18), [])


if __name__ == '__main__':
    unittest.main()
