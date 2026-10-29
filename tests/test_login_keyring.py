"""Protect encrypted credentials from accidental replacement during promotion."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    'keyring', Path(__file__).resolve().parents[1] / 'roles/login_keyring_migration/files/prepare-login-keyring.py'
)
keyring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keyring)


class KeyringPromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.directory = self.root / 'keyrings'
        self.directory.mkdir(mode=0o700)
        self.state = self.root / 'state'
        self.name = 'Standaard_sleutelbos'  # a localized default name
        self.old = self.directory / (self.name + '.keyring')
        self.content = keyring.HEADER + bytes(range(256))
        self.old.write_bytes(self.content)
        self.old.chmod(0o600)
        (self.directory / 'default').write_text(self.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_encrypted_content_and_backup_survive_and_repeat_is_noop(self):
        result = keyring.promote(self.directory, self.state)
        self.assertEqual((self.directory / 'login.keyring').read_bytes(), self.content)
        self.assertEqual((Path(result['backup']) / self.old.name).read_bytes(), self.content)
        self.assertEqual((self.directory / 'default').read_text(), 'login\n')
        self.assertFalse(self.old.exists())
        self.assertEqual(keyring.promote(self.directory, self.state), {'changed': False})

    def test_existing_login_keyring_is_never_replaced(self):
        new = self.directory / 'login.keyring'
        new.write_bytes(b'another encrypted collection')
        with self.assertRaises(RuntimeError):
            keyring.promote(self.directory, self.state)
        self.assertEqual(new.read_bytes(), b'another encrypted collection')
        self.assertEqual(self.old.read_bytes(), self.content)

    def test_plaintext_keyring_is_refused(self):
        self.old.write_bytes(b'[keyring]\ndisplay-name=Unencrypted\n')
        with self.assertRaises(RuntimeError):
            keyring.promote(self.directory, self.state)
        self.assertTrue(self.old.exists())

    def test_linked_source_is_refused(self):
        source = self.root / 'private-data'
        self.old.rename(source)
        self.old.symlink_to(source)
        with self.assertRaises(RuntimeError):
            keyring.promote(self.directory, self.state)
        self.assertEqual(source.read_bytes(), self.content)

    def test_any_default_name_is_promoted(self):
        (self.directory / 'default').write_text('Default_keyring\n')
        self.old.rename(self.directory / 'Default_keyring.keyring')
        self.assertTrue(keyring.promote(self.directory, self.state)['changed'])
        self.assertEqual((self.directory / 'login.keyring').read_bytes(), self.content)

    def test_a_default_without_its_file_or_outside_the_directory_is_refused(self):
        for name in ('../escape', '.hidden', 'missing'):
            (self.directory / 'default').write_text(name)
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                keyring.promote(self.directory, self.state)
        self.assertEqual(self.old.read_bytes(), self.content)

    def test_foreign_default_is_preserved(self):
        (self.directory / 'default').write_text('another-keyring')
        with self.assertRaises(RuntimeError):
            keyring.promote(self.directory, self.state)
        self.assertEqual((self.directory / 'default').read_text(), 'another-keyring')
        self.assertEqual(self.old.read_bytes(), self.content)


if __name__ == '__main__':
    unittest.main()
