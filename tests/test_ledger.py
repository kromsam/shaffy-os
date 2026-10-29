"""The ownership ledger's planning and crash-safe transitions."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy import ledger
from shaffy.records import RecordError

LINK = {'kind': 'wants-link', 'unit': 'a.service', 'link': '~/.config/systemd/user/default.target.wants/a.service'}
ENTRY = {'kind': 'autostart', 'path': '~/.config/autostart/A.desktop'}


def integration(state, activation=(LINK, ENTRY), scope='home'):
    return {'state': state, 'scope': scope, 'targets': [], 'activation': list(activation)}


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.book = ledger.Ledger(self.home / 'state')

    def activate(self):
        for record in (LINK, ENTRY):
            path = ledger.expand(record.get('link') or record['path'], self.home)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to('/dev/null')

    def test_an_empty_ledger_reads_as_nothing_managed(self):
        self.assertEqual(self.book.read({'uid': 1})['integrations'], {})

    def test_an_unknown_version_is_refused(self):
        self.book.write({'ledger_version': 9, 'integrations': {}})
        with self.assertRaises(RecordError):
            self.book.read()
        self.book.path.write_text('{"ledger_version": 1, ')
        with self.assertRaises(RecordError):
            self.book.read()

    def test_system_integrations_are_left_to_the_system_operation(self):
        result = ledger.plan({'x': integration('managed', scope='system')}, {'integrations': {}}, self.home, 'op1')
        self.assertEqual(result, ([], [], {}))

    def test_a_ledger_entry_missing_from_the_catalogue_is_refused(self):
        owned = {'integrations': {'gone': {'scope': 'home', 'state': 'managed', 'activation': []}}}
        problems, _, _ = ledger.plan({}, owned, self.home, 'op1')
        self.assertIn('integration gone release', problems[0])

    def test_disabled_and_released_without_an_entry_do_nothing(self):
        self.activate()
        integrations = {'a': integration('disabled'), 'b': integration('released')}
        self.assertEqual(ledger.plan(integrations, {'integrations': {}}, self.home, 'op1'), ([], [], {}))

    def test_disabling_resumes_its_remaining_records_after_a_crash(self):
        self.activate()
        owned = ledger.adopt({'integrations': {}}, 'a', integration('managed'), 'op0')
        _, transitions, _ = ledger.plan({'a': integration('disabled')}, owned, self.home, 'op1')
        self.assertEqual(
            ledger.removals(transitions, self.home),
            ['.config/systemd/user/default.target.wants/a.service', '.config/autostart/A.desktop'],
        )
        ledger.begin(owned, transitions, 'op1')
        writes = []

        def crash_after_first(data):
            writes.append(json.dumps(data))
            if len(writes) == 1:
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            ledger.finish(owned, 'op1', self.home, crash_after_first)
        stored = json.loads(writes[0])
        self.assertEqual(stored['integrations']['a']['transition']['remaining'], [ENTRY])
        # The resume plans the same transition again; the removed link counts as done.
        _, again, _ = ledger.plan({'a': integration('disabled')}, stored, self.home, 'op1')
        ledger.begin(stored, again, 'op1')
        self.assertEqual(ledger.finish(stored, 'op1', self.home, lambda data: None), [('a', 'disabled')])
        self.assertFalse(os.path.lexists(ledger.expand(ENTRY['path'], self.home)))
        self.assertEqual(stored['integrations']['a']['state'], 'disabled')
        self.assertIsNone(stored['integrations']['a']['transition'])

    def test_an_unmet_activation_precondition_defers_instead_of_refusing(self):
        receipt = self.home / 'receipt.json'
        gated = {
            **integration('managed'),
            'activate_requires': {
                'file': '~/receipt.json',
                'json': {'status': 'verified', 'repository_id': 'abc'},
                'hint': 'back up first',
            },
        }
        problems, transitions, deferred = ledger.plan({'a': gated}, {'integrations': {}}, self.home, 'op1')
        self.assertEqual((problems, transitions), ([], []))
        self.assertIn('back up first', deferred['a'])
        receipt.write_text('{"status": "verified", "repository_id": "other"}')
        self.assertIn('a', ledger.plan({'a': gated}, {'integrations': {}}, self.home, 'op1')[2])
        receipt.write_text('{"status": "verified", "repository_id": "abc"}')
        _, transitions, deferred = ledger.plan({'a': gated}, {'integrations': {}}, self.home, 'op1')
        self.assertEqual(([t['to'] for t in transitions], deferred), (['managed'], {}))
        unresolved = {**gated, 'activate_requires': {**gated['activate_requires'], 'json': {'repository_id': None}}}
        self.assertIn('a', ledger.plan({'a': unresolved}, {'integrations': {}}, self.home, 'op1')[2])

    def test_a_managed_entry_follows_a_changed_catalogue(self):
        owned = ledger.adopt({'integrations': {}}, 'a', integration('managed', activation=(LINK,)), 'op0')
        self.assertEqual(ledger.plan({'a': integration('managed', activation=(LINK,))}, owned, self.home, 'op1')[1], [])
        _, transitions, _ = ledger.plan({'a': integration('managed')}, owned, self.home, 'op1')
        self.assertEqual([(t['to'], len(t['activation'])) for t in transitions], [('managed', 2)])
        ledger.begin(owned, transitions, 'op1')
        ledger.finish(owned, 'op1', self.home, lambda data: None)
        self.assertEqual(owned['integrations']['a']['activation'], [LINK, ENTRY])


if __name__ == '__main__':
    unittest.main()
