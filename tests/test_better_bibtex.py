"""The Better BibTeX installer puts the pinned release into a managed Zotero profile once."""

import hashlib
import io
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
USER_JS = ROOT / 'home/private_dot_zotero/private_zotero/private_shaffy.default/private_user.js.tmpl'
PAYLOAD = b'better bibtex release'


class Response(io.BytesIO):
    def __enter__(self):
        return self


class BetterBibTeXTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.installer = runpy.run_path(str(ROOT / 'scripts/install-better-bibtex.py'))
        self.installer['main'].__globals__['SHA256'] = hashlib.sha256(PAYLOAD).hexdigest()
        self.target = self.home / self.installer['TARGET']
        self.profile = self.home / self.installer['PROFILE']
        self.profile.mkdir(parents=True)
        (self.profile / 'user.js').write_text(USER_JS.read_text())
        self.ledger('managed')

    def ledger(self, state):
        path = self.home / '.local/state/shaffy/ledger.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'ledger_version': 1, 'integrations': {'zotero': {'state': state}}}))

    def install(self, payload=PAYLOAD):
        with (
            mock.patch.dict('os.environ', {'HOME': str(self.home), 'XDG_STATE_HOME': str(self.home / '.local/state')}),
            mock.patch.dict(self.installer['main'].__globals__, fetch=mock.Mock(return_value=Response(payload))),
            mock.patch('sys.stdout', new_callable=io.StringIO),
        ):
            self.installer['main']()
            return self.installer['main'].__globals__['fetch']

    def test_the_pinned_release_is_installed_once(self):
        fetch = self.install()
        fetch.assert_called_once_with(self.installer['URL'])
        self.assertIn(f'/v{self.installer["VERSION"]}/', self.installer['URL'])
        self.assertEqual(self.target.read_bytes(), PAYLOAD)
        self.assertEqual(sorted(p.name for p in self.target.parent.iterdir()), [self.target.name])
        self.install().assert_not_called()
        self.assertEqual(self.target.read_bytes(), PAYLOAD)

    def test_a_download_that_does_not_match_the_pin_is_refused(self):
        with self.assertRaisesRegex(SystemExit, 'does not match the pinned SHA-256'):
            self.install(b'tampered')
        self.assertFalse(self.target.exists())
        self.assertEqual(sorted(p.name for p in self.profile.iterdir()), ['user.js'])

    def test_a_copy_zotero_has_updated_is_left_alone(self):
        self.target.parent.mkdir()
        self.target.write_bytes(b'a newer release')
        self.install().assert_not_called()
        self.assertEqual(self.target.read_bytes(), b'a newer release')

    def test_nothing_is_installed_unless_the_zotero_integration_is_managed(self):
        for state in ('disabled', None):
            with self.subTest(state=state):
                self.ledger(state)
                self.install().assert_not_called()
                self.assertFalse(self.target.exists())

    def test_it_waits_for_the_home_apply_that_lets_zotero_enable_it(self):
        # Before that apply Zotero would record the add-on as disabled, and the pref cannot undo that.
        for user_js in ('', 'user_pref("extensions.autoDisableScopes", 15);\n'):
            with self.subTest(user_js=user_js):
                (self.profile / 'user.js').write_text(user_js)
                with self.assertRaisesRegex(SystemExit, r'apply --home first'):
                    self.install()
                self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
