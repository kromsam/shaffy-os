"""Binding record, journal, lock and the supplement commands."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy.records import LockedError, RecordError, Records, new_record

BINDING = {'mode': 'supplement', 'path': '/somewhere', 'supplement_id': 'demo'}


class RecordsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.dir = Path(self.temporary.name) / 'shaffy'
        self.records = Records(self.dir)

    def test_write_is_private_and_leaves_no_temporary_files(self):
        self.records.write(new_record(BINDING, ROOT))
        self.assertEqual(self.records.read()['binding'], BINDING)
        self.assertEqual(oct(self.records.binding.stat().st_mode & 0o777), '0o600')
        self.assertEqual(list(self.dir.glob('.binding-*')), [])

    def test_unknown_record_version_is_refused(self):
        self.records.write(new_record(BINDING, ROOT))
        data = json.loads(self.records.binding.read_text())
        data['record_version'] = 99
        self.records.binding.write_text(json.dumps(data))
        with self.assertRaisesRegex(RecordError, 'record_version 99'):
            self.records.read()

    def test_journal_appends_in_order(self):
        self.records.append('op-1', 'attach')
        self.records.append('op-2', 'rebind', to='x')
        self.assertEqual([e['event'] for e in self.records.events()], ['attach', 'rebind'])
        self.assertEqual(self.records.events(limit=1)[0]['to'], 'x')

    def test_a_torn_line_is_skipped_and_the_next_event_starts_on_its_own_line(self):
        self.records.append('op-1', 'begin')
        with open(self.records.journal, 'a') as stream:
            stream.write('{"at": "2026-09-23", "op": "op-1", "ev')  # a crash mid-write
        self.records.append('op-2', 'attach')
        self.assertEqual([e['event'] for e in self.records.events()], ['begin', 'attach'])
        self.assertEqual(len(self.records.torn), 1)
        self.assertIn('op-1', self.records.torn[0])

    def test_second_holder_of_the_lock_fails_at_once(self):
        with self.records.lock():
            with self.assertRaisesRegex(LockedError, 'another Shaffy OS operation'):
                with Records(self.dir).lock():
                    pass
        with Records(self.dir).lock():
            pass  # released after the first holder left

    def test_record_of_another_target_is_refused(self):
        record = new_record(BINDING, ROOT)
        record['target'] = {'uid': 4242, 'user': 'someone', 'home': '/home/someone'}
        with self.assertRaisesRegex(RecordError, 'bound for someone'):
            self.records.check_target(record)

    def test_an_unreadable_root_state_names_the_fix(self):
        from unittest import mock

        from shaffy.records import read_root_record

        with (
            tempfile.TemporaryDirectory() as root,
            mock.patch.dict(os.environ, {'SHAFFY_TEST_PREREQUISITES': 'fake', 'SHAFFY_TEST_ROOT_STATE': root}),
        ):
            (Path(root) / 'targets').mkdir()
            (Path(root) / 'targets/4242.json').write_text('{}')
            os.chmod(root, 0o600)  # not searchable, as /var/lib/shaffy at 0700 is for a user
            try:
                with self.assertRaisesRegex(RecordError, 'sudo chmod 755'):
                    read_root_record(4242)
            finally:
                os.chmod(root, 0o700)

    def kill_during_write(self, point):
        script = (
            f'import os, signal, sys; sys.path.insert(0, {str(ROOT / "lib")!r})\n'
            'from shaffy.records import Records, new_record\n'
            f'r = Records({str(self.dir)!r}, fault=lambda p: os.kill(os.getpid(), '
            f'signal.SIGKILL) if p == {point!r} else None)\n'
            "r.write(new_record({'mode': 'none', 'path': None, 'supplement_id': None}, '/x'))\n"
        )
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, check=False)
        self.assertEqual(result.returncode, -signal.SIGKILL)

    def test_kill_before_rename_keeps_the_old_record(self):
        self.records.write(new_record(BINDING, ROOT))
        self.kill_during_write('before-replace')
        self.assertEqual(self.records.read()['binding'], BINDING)
        self.assertTrue(list(self.dir.glob('.binding-*.tmp')))  # the crash left its temporary file
        self.records.write(new_record(BINDING, ROOT))
        self.assertEqual(list(self.dir.glob('.binding-*.tmp')), [])  # the next write cleans it

    def test_kill_after_rename_keeps_the_new_record(self):
        self.records.write(new_record(BINDING, ROOT))
        self.kill_during_write('after-replace')
        self.assertEqual(self.records.read()['binding']['mode'], 'none')


class SupplementCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / 'state'
        # Never the machine's own root records: those belong to its real installation.
        self.env = {
            **os.environ,
            'XDG_STATE_HOME': str(self.state),
            'PATH': '/usr/bin:/bin',
            'SHAFFY_TEST_PREREQUISITES': 'fake',
            'SHAFFY_TEST_ROOT_STATE': str(Path(self.temporary.name) / 'root'),
        }

    def shaffy(self, *arguments):
        return subprocess.run(
            [str(ROOT / 'bin/shaffy'), *arguments], capture_output=True, text=True, env=self.env, check=False
        )

    def root_record(self, binding):
        """A root record as a system apply leaves it, in a test root state directory."""
        import pwd

        root = Path(self.temporary.name) / 'root'
        (root / 'targets').mkdir(parents=True)
        self.env.update(SHAFFY_TEST_PREREQUISITES='fake', SHAFFY_TEST_ROOT_STATE=str(root))
        uid = os.getuid()
        (root / f'targets/{uid}.json').write_text(
            json.dumps(
                {
                    'record_version': 1,
                    'target': {'uid': uid, 'user': pwd.getpwuid(uid).pw_name, 'home': str(Path.home())},
                    'binding': binding,
                    'pending': None,
                    'last_completed_op': 'op-0',
                    'phases': {},
                }
            )
        )

    def test_a_root_record_is_adopted_only_when_it_agrees(self):
        supplement = ROOT / 'supplement.example'
        selection = json.loads(self.shaffy('data', '--supplement', str(supplement)).stdout)['shaffy']['selection']
        self.root_record({'mode': 'supplement', 'path': selection['path'], 'supplement_id': selection['supplement_id']})
        refused = self.shaffy('supplement', 'attach', '--supplement', str(supplement))
        self.assertEqual(refused.returncode, 1)
        self.assertIn('--adopt-system-record', refused.stderr)
        other = self.shaffy('supplement', 'attach', '--none', '--adopt-system-record')
        self.assertEqual(other.returncode, 1)
        self.assertIn('binding.mode', other.stderr)
        adopted = self.shaffy('supplement', 'attach', '--supplement', str(supplement), '--adopt-system-record')
        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        record = json.loads((self.state / 'shaffy/binding.json').read_text())
        self.assertTrue(record['system']['bound'])

    def test_adoption_waits_for_an_operation_root_holds(self):
        self.root_record({'mode': 'none', 'path': None, 'supplement_id': None})
        path = Path(self.env['SHAFFY_TEST_ROOT_STATE']) / f'targets/{os.getuid()}.json'
        record = json.loads(path.read_text())
        record['pending'] = {'op': 'op-root'}
        path.write_text(json.dumps(record))
        refused = self.shaffy('supplement', 'attach', '--none', '--adopt-system-record')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('apply --system --abandon', refused.stderr)

    def test_adoption_ignores_the_path_but_not_the_supplement_id(self):
        supplement = ROOT / 'supplement.example'
        selection = json.loads(self.shaffy('data', '--supplement', str(supplement)).stdout)['shaffy']['selection']
        self.root_record({'mode': 'supplement', 'path': '/where/it/was', 'supplement_id': 'another-id'})
        refused = self.shaffy('supplement', 'attach', '--supplement', str(supplement), '--adopt-system-record')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('binding.supplement_id', refused.stderr)
        self.assertNotIn('binding.path', refused.stderr)

    def test_attach_then_refuse_a_second_attach(self):
        first = self.shaffy('supplement', 'attach', '--supplement', str(ROOT / 'supplement.example'))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn('Nothing was applied', first.stdout)
        second = self.shaffy('supplement', 'attach', '--none')
        self.assertEqual(second.returncode, 1)
        self.assertIn('already bound', second.stderr)
        record = json.loads((self.state / 'shaffy/binding.json').read_text())
        self.assertEqual(record['binding']['supplement_id'], 'my-desktop')
        self.assertIsNone(record['pending'])

    def test_attach_refuses_invalid_settings_and_writes_nothing(self):
        bad = Path(self.temporary.name) / 'bad'
        bad.mkdir()
        (bad / 'config.yaml').write_text('schema_version: 1\nsupplement_id: x\ncolour: blue\n')
        result = self.shaffy('supplement', 'attach', '--supplement', str(bad))
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown key 'colour'", result.stderr)
        self.assertFalse((self.state / 'shaffy/binding.json').exists())

    def test_rebind_requires_the_same_supplement_id_unless_told(self):
        self.shaffy('supplement', 'attach', '--supplement', str(ROOT / 'supplement.example'))
        other = ROOT / 'tests/fixtures/supplements/comprehensive'
        refused = self.shaffy('supplement', 'rebind', '--path', str(other))
        self.assertEqual(refused.returncode, 1)
        self.assertIn('--new-id', refused.stderr)
        accepted = self.shaffy('supplement', 'rebind', '--path', str(other), '--new-id')
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        events = (self.state / 'shaffy/journal.jsonl').read_text().splitlines()
        self.assertEqual([json.loads(e)['event'] for e in events], ['attach', 'rebind'])

    def test_bound_selection_is_what_the_loader_uses(self):
        self.shaffy('supplement', 'attach', '--supplement', str(ROOT / 'supplement.example'))
        data = json.loads(self.shaffy('data').stdout)
        self.assertEqual(data['shaffy']['selection']['source'], 'binding')
        status = self.shaffy('status', '--no-drift')
        self.assertIn('Binding: supplement my-desktop', status.stdout)
        self.assertIn('Unfinished operation: none', status.stdout)


if __name__ == '__main__':
    unittest.main()
