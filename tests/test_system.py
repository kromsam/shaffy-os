"""The system operation's root steps and records.

These run lib/shaffy/root_op.py in-process as the current user against a
temporary state directory with the fake snapper; the privileged container test
runs the whole route as root.
"""

from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy import root_op
from shaffy.capture import capture
from shaffy.loader import load
from shaffy.records import disagreements

FIXTURE = ROOT / 'tests/fixtures/supplements/comprehensive'


class RootStepTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.tmp = Path(self.temporary.name)
        self.supplement = self.tmp / 'supplement'
        shutil.copytree(FIXTURE, self.supplement)
        selection = {'mode': 'supplement', 'path': str(self.supplement), 'source': 'binding'}
        self.manifest = capture(ROOT, selection, self.tmp / 'user-state', 'op-1', load)
        self.capture = Path(self.manifest['directory'])
        self.state = self.tmp / 'root-state'
        patcher = mock.patch.object(root_op, '_in_container', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_step(self, action, op='op-1', uid=4242, capture_dir=None, sha=None):
        arguments = [
            action,
            '--op',
            op,
            '--uid',
            str(uid),
            '--test-prerequisites',
            'fake',
            '--state-dir',
            str(self.state),
        ]
        if action == 'begin':
            directory = Path(capture_dir or self.capture)
            arguments += [
                '--user',
                'tester',
                '--home',
                '/home/tester',
                '--capture',
                str(directory),
                '--manifest-sha256',
                sha or hashlib.sha256((directory / 'manifest.json').read_bytes()).hexdigest(),
            ]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = root_op.main(arguments)
        return code, (json.loads(out.getvalue()) if code == 0 else None), err.getvalue()

    def record(self, uid=4242):
        return json.loads((self.state / f'targets/{uid}.json').read_text())

    def events(self, uid=4242):
        lines = (self.state / f'targets/{uid}.journal.jsonl').read_text().splitlines()
        return [json.loads(line)['event'] for line in lines]

    def test_begin_copies_verifies_and_takes_the_lock(self):
        code, result, err = self.run_step('begin')
        self.assertEqual(code, 0, err)
        copy_dir = self.state / 'ops/op-1/capture'
        self.assertEqual(
            (copy_dir / 'supplement/config.yaml').read_bytes(), (self.capture / 'supplement/config.yaml').read_bytes()
        )
        self.assertEqual(oct(copy_dir.stat().st_mode & 0o777), '0o700')
        record = self.record()
        self.assertEqual(record['pending']['op'], 'op-1')
        self.assertEqual(
            record['binding'],
            {'mode': 'supplement', 'path': str(self.supplement), 'supplement_id': 'synthetic-desktop'},
        )
        for readable in ('targets/4242.json', 'targets/4242.journal.jsonl'):  # the user side reads both
            self.assertEqual(oct((self.state / readable).stat().st_mode & 0o777), '0o644', readable)
        # The pre snapshots come before root `pending`, and the record before its journal line.
        self.assertEqual(self.events(), ['snapper-home-created', 'begin', 'phase-start'])
        self.assertEqual(result['pre'], {'root': 1, 'home': 2})
        # The play reads the verified copy, whose file references point into the copy too.
        play = json.loads(Path(result['settings_file']).read_text())['shaffy']
        self.assertTrue(all(path.startswith(str(copy_dir)) for path in play['overrides'].values()))
        self.assertIn('fish', play['overrides'])

    def test_a_system_operation_takes_no_dotfiles_even_broken_ones(self):
        (self.tmp / 'outside.txt').write_text('secret\n')
        (self.supplement / 'dotfiles/dot_outside').symlink_to(self.tmp / 'outside.txt')
        selection = {'mode': 'supplement', 'path': str(self.supplement), 'source': 'binding'}
        broken = capture(ROOT, selection, self.tmp / 'user-state', 'op-2', load)
        code, _result, err = self.run_step('begin', op='op-2', capture_dir=broken['directory'])
        self.assertEqual(code, 0, err)
        copied = self.state / 'ops/op-2/capture/supplement'
        self.assertEqual([path for path in copied.rglob('*') if 'dotfiles' in path.parts and path.is_file()], [])
        self.assertFalse(any(name.startswith('supplement/dotfiles/') for name in broken['files']))

    def test_finish_pairs_the_snapshots_and_completes(self):
        self.run_step('begin')
        code, result, err = self.run_step('finish')
        self.assertEqual(code, 0, err)
        self.assertEqual(result['post'], {'root': 3, 'home': 4})
        record = self.record()
        self.assertIsNone(record['pending'])
        self.assertEqual(record['last_completed_op'], 'op-1')
        self.assertEqual(record['phases']['system']['op'], 'op-1')
        self.assertEqual(self.events()[-2:], ['phase-done', 'complete'])
        log = [json.loads(line) for line in (self.state / 'targets/fake-snapshots.jsonl').read_text().splitlines()]
        self.assertEqual(
            [(e['config'], e['type'], e['pre']) for e in log],
            [('root', 'pre', None), ('home', 'pre', None), ('root', 'post', 1), ('home', 'post', 2)],
        )

    def test_finish_keeps_the_newest_ten_root_copies(self):
        for number in range(12):
            (self.state / f'ops/20000101T0000{number:02d}Z-old/capture').mkdir(parents=True)
        (self.state / 'ops/20000101T000013Z-rebind').mkdir()  # a record change's helper directory
        self.run_step('begin')
        self.run_step('finish')
        kept = sorted(p.name for p in (self.state / 'ops').iterdir())
        self.assertEqual(len(kept), 10)
        self.assertIn('op-1', kept)
        self.assertNotIn('20000101T000000Z-old', kept)
        self.assertNotIn('20000101T000013Z-rebind', kept)

    def test_an_abandoned_copy_is_never_pruned(self):
        self.run_step('begin', op='00000000T000000Z-abandoned')
        self.run_step('abandon', op='00000000T000000Z-abandoned')
        for number in range(11):
            op = f'op-{number + 10}'
            other = capture(
                ROOT,
                {'mode': 'supplement', 'path': str(self.supplement), 'source': 'binding'},
                self.tmp / 'user-state',
                op,
                load,
            )
            self.run_step('begin', op=op, capture_dir=other['directory'])
            self.run_step('finish', op=op)
        self.assertTrue((self.state / 'ops/00000000T000000Z-abandoned/capture').is_dir())

    def test_a_second_begin_of_the_same_operation_resumes(self):
        self.run_step('begin')
        code, result, err = self.run_step('begin')
        self.assertEqual(code, 0, err)
        self.assertTrue(result['resumed'])
        self.assertEqual(self.events(), ['snapper-home-created', 'begin', 'phase-start', 'resume-start'])
        self.run_step('finish')
        self.assertEqual(
            json.loads((self.state / 'targets/fake-snapshots.jsonl').read_text().splitlines()[-1])['pre'], 4
        )  # paired with the resume's pre

    def test_another_unfinished_operation_refuses(self):
        self.run_step('begin')
        second = capture(
            ROOT, {'mode': 'none', 'path': None, 'source': 'binding'}, self.tmp / 'user-state', 'op-2', load
        )
        code, _, err = self.run_step('begin', op='op-2', capture_dir=second['directory'])
        self.assertEqual(code, 1)
        self.assertIn('system operation op-1 is unfinished', err)
        code, _, err = self.run_step('begin', op='op-2', uid=4343, capture_dir=second['directory'])
        self.assertEqual(code, 1)
        self.assertIn('of another user is unfinished', err)

    def test_a_changed_capture_is_refused(self):
        (self.capture / 'supplement/config.yaml').write_text('# changed after the capture\n')
        code, _, err = self.run_step('begin', sha=self.manifest_digest())
        self.assertEqual(code, 1)
        self.assertIn('does not match its manifest', err)
        self.assertFalse((self.state / 'targets/4242.json').exists())

    def manifest_digest(self):
        return hashlib.sha256((self.capture / 'manifest.json').read_bytes()).hexdigest()

    def test_a_manifest_other_than_the_wrappers_is_refused(self):
        code, _, err = self.run_step('begin', sha='0' * 64)
        self.assertEqual(code, 1)
        self.assertIn('does not match the digest the wrapper passed', err)

    def test_extra_files_and_links_are_refused(self):
        (self.capture / 'extra.txt').write_text('x\n')
        self.assertIn('other files than its manifest', self.run_step('begin')[2])
        (self.capture / 'extra.txt').unlink()
        (self.capture / 'link').symlink_to('/etc/hostname')
        self.assertIn('something other than a file', self.run_step('begin')[2])
        (self.capture / 'link').unlink()
        (self.capture / 'directory-link').symlink_to('/etc', target_is_directory=True)
        self.assertIn('a symbolic link: directory-link', self.run_step('begin')[2])
        (self.capture / 'directory-link').unlink()
        os.mkfifo(self.capture / 'fifo')  # checked after opening, never read
        self.assertIn('something other than a file', self.run_step('begin')[2])

    def test_an_override_named_like_the_manifest_is_an_ordinary_file(self):
        (self.supplement / 'overrides/niri.kdl').rename(self.supplement / 'overrides/manifest.json')
        config = self.supplement / 'config.yaml'
        config.write_text(config.read_text().replace('niri: overrides/niri.kdl', 'niri: overrides/manifest.json'))
        selection = {'mode': 'supplement', 'path': str(self.supplement), 'source': 'binding'}
        manifest = capture(ROOT, selection, self.tmp / 'user-state', 'op-5', load)
        from shaffy.capture import verify

        verify(manifest['directory'])
        code, _, err = self.run_step('begin', op='op-5', capture_dir=manifest['directory'])
        self.assertEqual(code, 0, err)

    def test_operation_ids_and_references_cannot_leave_their_directories(self):
        code, _, err = self.run_step('begin', op='../outside')
        self.assertEqual(code, 1)
        self.assertIn('is not an operation ID', err)
        self.assertFalse((self.tmp / 'outside').exists())
        settings = json.loads((self.capture / 'settings.json').read_text())
        settings['shaffy']['overrides']['niri'] = str(self.capture) + '/../../../../etc/hostname'
        (self.capture / 'settings.json').write_text(json.dumps(settings))
        manifest = json.loads((self.capture / 'manifest.json').read_text())
        manifest['files']['settings.json'] = hashlib.sha256((self.capture / 'settings.json').read_bytes()).hexdigest()
        (self.capture / 'manifest.json').write_text(json.dumps(manifest))
        code, _, err = self.run_step('begin')
        self.assertEqual(code, 1)
        self.assertIn('lies outside the capture', err)

    def test_abandon_clears_root_pending_and_keeps_the_copy(self):
        self.run_step('begin')
        code, result, err = self.run_step('abandon')
        self.assertEqual(code, 0, err)
        self.assertIsNone(self.record()['pending'])
        self.assertEqual(result['pre_snapshots'], [{'root': 1, 'home': 2}])
        self.assertTrue((self.state / 'ops/op-1/capture').is_dir())
        self.assertEqual(self.events()[-1], 'abandoned')
        self.assertIn('not the unfinished system operation', self.run_step('abandon')[2])

    def test_rebind_moves_the_root_binding_only_between_operations(self):
        self.run_step('begin')
        arguments = [
            'rebind',
            '--op',
            'op-2',
            '--uid',
            '4242',
            '--mode',
            'supplement',
            '--path',
            '/new',
            '--supplement-id',
            'synthetic-desktop',
            '--test-prerequisites',
            'fake',
            '--state-dir',
            str(self.state),
        ]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(root_op.main(arguments), 1)
        self.assertIn('is unfinished', err.getvalue())
        self.run_step('finish')
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(root_op.main(arguments), 0)
        self.assertEqual(self.record()['binding']['path'], '/new')

    def test_a_second_target_user_is_added_to_the_home_configuration(self):
        self.run_step('begin')
        self.run_step('finish')
        second = capture(
            ROOT, {'mode': 'none', 'path': None, 'source': 'binding'}, self.tmp / 'user-state', 'op-6', load
        )
        arguments = [
            'begin',
            '--op',
            'op-6',
            '--uid',
            '4343',
            '--user',
            'second',
            '--home',
            '/home/second',
            '--capture',
            second['directory'],
            '--manifest-sha256',
            hashlib.sha256((Path(second['directory']) / 'manifest.json').read_bytes()).hexdigest(),
            '--test-prerequisites',
            'fake',
            '--state-dir',
            str(self.state),
        ]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(root_op.main(arguments), 0)
        self.assertEqual((self.state / 'targets/fake-home-users').read_text().split(), ['tester', 'second'])
        self.assertIn('snapper-home-configured', self.events(4343))

    def test_a_different_binding_is_refused(self):
        self.run_step('begin')
        self.run_step('finish')
        other = capture(
            ROOT, {'mode': 'none', 'path': None, 'source': 'binding'}, self.tmp / 'user-state', 'op-3', load
        )
        code, _, err = self.run_step('begin', op='op-3', capture_dir=other['directory'])
        self.assertEqual(code, 1)
        self.assertIn('supplement rebind', err)

    def test_finish_without_the_operation_refuses(self):
        code, _, err = self.run_step('finish')
        self.assertEqual(code, 1)
        self.assertIn('not the unfinished system operation', err)

    def test_the_test_adapter_is_refused_outside_a_container(self):
        with mock.patch.object(root_op, '_in_container', return_value=False):
            code, _, err = self.run_step('begin')
        self.assertEqual(code, 1)
        self.assertIn('refused outside a container', err)


class AgreementTests(unittest.TestCase):
    USER = {
        'target': {'uid': 1000, 'user': 'u', 'home': '/home/u'},
        'binding': {'mode': 'supplement', 'path': '/s', 'supplement_id': 'id'},
        'pending': None,
    }

    def test_equal_records_agree_and_every_compared_field_counts(self):
        root = copy.deepcopy(self.USER)
        self.assertEqual(disagreements(self.USER, root), [])
        for path, value in (
            (('target', 'uid'), 1001),
            (('target', 'home'), '/x'),
            (('binding', 'mode'), 'none'),
            (('binding', 'path'), '/t'),
            (('binding', 'supplement_id'), 'other'),
        ):
            changed = copy.deepcopy(root)
            changed[path[0]][path[1]] = value
            with self.subTest(field='.'.join(path)):
                self.assertEqual(len(disagreements(self.USER, changed)), 1)

    def test_system_operations_must_match_and_home_ones_do_not_count(self):
        user = dict(self.USER, pending={'op': 'a', 'scope': 'home'})
        self.assertEqual(disagreements(user, dict(self.USER, pending=None)), [])
        user = dict(self.USER, pending={'op': 'a', 'scope': 'system'})
        self.assertEqual(disagreements(user, dict(self.USER, pending={'op': 'a'})), [])
        self.assertTrue(disagreements(user, dict(self.USER, pending=None)))
        self.assertTrue(disagreements(self.USER, dict(self.USER, pending={'op': 'b'})))


class WrapperTests(unittest.TestCase):
    """The wrapper's decisions from the user and root records, without running a play."""

    def setUp(self):
        from shaffy import system
        from shaffy.records import Records, new_record
        from shaffy.source import manifest

        self.system = system
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        tmp = Path(self.temporary.name)
        self.root_state = tmp / 'root'
        (self.root_state / 'targets').mkdir(parents=True)
        environment = mock.patch.dict(
            os.environ,
            {
                'HOME': str(tmp / 'home'),
                'XDG_STATE_HOME': str(tmp / 'home/.local/state'),
                'SHAFFY_TEST_PREREQUISITES': 'fake',
                'SHAFFY_TEST_ROOT_STATE': str(self.root_state),
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        container = mock.patch.object(system, '_in_container', return_value=True)
        container.start()
        self.addCleanup(container.stop)
        self.records = Records()
        record = new_record({'mode': 'none', 'path': None, 'supplement_id': None}, ROOT)
        self.uid = record['target']['uid']
        self.user = record
        self.source = manifest(ROOT)

    def pending(self, op='op-9'):
        self.user['pending'] = {
            'op': op,
            'scope': 'system',
            'capture': '/nonexistent',
            'phase': 'system',
            'started_at': 'x',
            'checkout': str(ROOT),
            'settings_sha256': 'x',
            'source': self.source,
        }
        self.records.write(self.user)

    def root(self, **fields):
        record = {
            'record_version': 1,
            'target': self.user['target'],
            'binding': self.user['binding'],
            'pending': None,
            'last_completed_op': None,
            'phases': {},
            **fields,
        }
        (self.root_state / f'targets/{self.uid}.json').write_text(json.dumps(record))

    def root_journal(self, *events, op='op-9'):
        with open(self.root_state / f'targets/{self.uid}.journal.jsonl', 'a') as journal:
            for event in events:
                journal.write(json.dumps({'at': 'x', 'op': op, 'event': event}) + '\n')

    def resume(self):
        messages = []
        return self.system.SystemApply(ROOT, out=messages.append).resume(), messages

    def test_an_operation_root_never_took_is_cleared(self):
        self.pending()
        code, messages = self.resume()
        self.assertEqual(code, 0)
        self.assertIsNone(self.records.read()['pending'])
        self.assertIn('had not reached root', messages[0])

    def test_an_operation_root_completed_is_completed_for_the_user(self):
        self.pending()
        self.root_journal('begin', 'phase-start', 'phase-done', 'complete')
        self.root(last_completed_op='op-9')
        code, _ = self.resume()
        self.assertEqual(code, 0)
        record = self.records.read()
        self.assertIsNone(record['pending'])
        self.assertTrue(record['system']['bound'])
        self.assertEqual(record['last_completed_op'], 'op-9')

    def test_an_operation_root_does_not_hold_is_cleared_even_with_a_journal_line(self):
        # Root writes its record before its journal, so a `begin` line without root pending means
        # root took nothing that needs finishing.
        self.pending()
        self.root_journal('begin')
        self.root(pending={'op': 'op-other'})
        code, _ = self.resume()
        self.assertEqual(code, 0)
        self.assertIsNone(self.records.read()['pending'])

    def test_a_record_for_another_target_is_not_resumed(self):
        from shaffy.records import RecordError

        self.user['target'] = {**self.user['target'], 'uid': self.uid + 1, 'home': '/home/second'}
        self.pending()
        with self.assertRaisesRegex(RecordError, 'is bound for'):
            self.resume()
        self.assertIsNotNone(self.records.read()['pending'])

    def test_a_started_operation_root_no_longer_holds_refuses(self):
        self.pending()
        self.root_journal('begin', 'phase-start')
        self.root()
        with self.assertRaisesRegex(self.system.ApplyError, 'rolled back'):
            self.resume()

    def test_abandoning_an_operation_root_never_took_clears_the_user_side(self):
        self.pending()
        messages = []
        self.assertEqual(self.system.SystemApply(ROOT, out=messages.append).abandon(), 0)
        self.assertIsNone(self.records.read()['pending'])
        self.assertIn('nothing was deleted', messages[-1])

    def test_abandoning_what_root_completed_completes_it(self):
        self.pending()
        self.root(last_completed_op='op-9')
        self.assertEqual(self.system.SystemApply(ROOT, out=lambda _: None).abandon(), 0)
        record = self.records.read()
        self.assertTrue(record['system']['bound'])
        self.assertEqual(record['last_completed_op'], 'op-9')

    def test_a_root_operation_is_abandoned_without_a_user_record(self):
        self.root(pending={'op': 'op-root'})
        plays = []
        with (
            mock.patch.object(self.system.bootstrap, 'problems', return_value=[]),
            mock.patch.object(
                self.system.SystemApply,
                '_run_play',
                side_effect=lambda playbook, operation, tags=None: plays.append((playbook, operation)) or 0,
            ),
        ):
            self.assertEqual(self.system.SystemApply(ROOT, out=lambda _: None).abandon(), 0)
        self.assertEqual(plays[0][0], 'records.yml')
        self.assertEqual((plays[0][1]['op'], plays[0][1]['action']), ('op-root', 'abandon'))
        self.assertIsNone(self.records.read())

    def test_a_changed_supplement_id_refuses_before_anything_is_written(self):
        supplement = Path(self.temporary.name) / 'supplement'
        shutil.copytree(FIXTURE, supplement)
        self.user['binding'] = {'mode': 'supplement', 'path': str(supplement), 'supplement_id': 'synthetic-desktop'}
        self.records.write(self.user)
        config = supplement / 'config.yaml'
        config.write_text(config.read_text().replace('supplement_id: synthetic-desktop', 'supplement_id: renamed'))
        with (
            mock.patch.object(self.system.bootstrap, 'problems', return_value=[]),
            self.assertRaisesRegex(self.system.ApplyError, 'rebind --path .* --new-id'),
        ):
            self.system.SystemApply(ROOT, out=lambda _: None).start('op-10')
        self.assertIsNone(self.records.read()['pending'])
        self.assertFalse(list((Path(self.temporary.name) / 'home/.local/state/shaffy/runs').glob('*')))

    def test_a_home_apply_after_an_unfinished_first_system_operation_names_that_operation(self):
        from shaffy.apply import ApplyError, HomeApply

        self.pending()  # the root record exists, but the user record is not marked bound yet
        self.root(pending={'op': 'op-9'})
        with self.assertRaisesRegex(ApplyError, 'operation op-9 is unfinished; run ./bin/shaffy apply --resume'):
            HomeApply(ROOT, None, lambda _: None).start('op-10')

    def test_an_unknown_root_record_names_the_adoption(self):
        self.records.write(self.user)
        self.root()
        with self.assertRaisesRegex(self.system.ApplyError, 'attach --adopt-system-record'):
            self.system.SystemApply(ROOT, out=lambda _: None).start('op-10')

    def test_disagreeing_records_refuse(self):
        self.user['system']['bound'] = True
        self.records.write(self.user)
        self.root(binding={'mode': 'supplement', 'path': '/elsewhere', 'supplement_id': 'x'})
        with self.assertRaisesRegex(self.system.ApplyError, 'records disagree'):
            self.system.SystemApply(ROOT, out=lambda _: None).start('op-10')

    def test_the_test_adapter_is_refused_outside_a_container(self):
        with (
            mock.patch.object(self.system, '_in_container', return_value=False),
            self.assertRaisesRegex(self.system.ApplyError, 'outside a container'),
        ):
            self.system.SystemApply(ROOT)


if __name__ == '__main__':
    unittest.main()
