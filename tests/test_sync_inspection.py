"""The sync inspectors report the clients' own state and resume only what is named."""
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('inspect_nextcloud', ROOT / 'scripts/inspect-nextcloud-runtime.py')
nextcloud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nextcloud)

CONFIG = r"""[General]
clientVersion=34.0.4

[Accounts]
0\url=https://cloud.example.invalid/
0\Folders\1\localPath=/home/tester/Documenten/
0\Folders\1\targetPath=/documents
0\Folders\1\paused=true
0\Folders\2\localPath=/home/tester/Sjablonen/
0\Folders\2\targetPath=/templates
"""


class NextcloudInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        (self.home / '.config/Nextcloud').mkdir(parents=True)
        (self.home / '.config/Nextcloud/nextcloud.cfg').write_text(CONFIG)

    def test_saved_accounts_folders_and_pauses_are_read_as_the_client_wrote_them(self):
        urls, folders = nextcloud.saved_folders(self.home / '.config/Nextcloud/nextcloud.cfg')
        self.assertEqual(urls, ['https://cloud.example.invalid'])
        self.assertEqual(folders, {'/home/tester/Documenten': {'remote': '/documents', 'paused': True},
                                   '/home/tester/Sjablonen': {'remote': '/templates', 'paused': False}})
        self.assertEqual(nextcloud.saved_folders(self.home / 'missing.cfg'), ([], {}))

    def test_a_folder_the_configuration_shows_running_is_not_resumed(self):
        objects = [{'/f/1': {nextcloud.ACCOUNT: {'Path': {'data': '/home/tester/Sjablonen/'}}}}]
        calls = []

        def bus(*args):
            calls.append(args)
            if args[:2] == ('call', nextcloud.BUS) and args[4] == 'GetManagedObjects':
                return objects
            if args[0] == 'call' and args[4] == 'Describe':
                return [[True, '', [{'type': 'b', 'data': True}]]]  # the unreliable checkbox says paused
            return 'Idle'
        errors = io.StringIO()
        with mock.patch.object(Path, 'home', return_value=self.home), mock.patch.object(nextcloud, 'bus', bus), \
                mock.patch.object(nextcloud, 'home_pair', side_effect=AssertionError('no snapshot expected')), \
                redirect_stdout(io.StringIO()), redirect_stderr(errors), \
                mock.patch.dict(os.environ, {'XDG_STATE_HOME': str(self.home / 'state')}):
            self.assertEqual(nextcloud.main(['--resume', '/home/tester/Sjablonen']), 0)
        self.assertIn('is not paused in the saved configuration', errors.getvalue())
        self.assertFalse([c for c in calls if 'Activate' in c])

    def test_resuming_a_folder_that_is_not_configured_touches_nothing(self):
        errors = io.StringIO()
        with mock.patch.object(Path, 'home', return_value=self.home), \
                mock.patch.object(nextcloud, 'bus', side_effect=AssertionError('no D-Bus call expected')), \
                redirect_stderr(errors):
            self.assertEqual(nextcloud.main(['--resume', '/home/tester/Muziek']), 1)
        self.assertIn('is not a configured Nextcloud folder', errors.getvalue())

    def test_an_unconfigured_client_is_reported_without_d_bus(self):
        (self.home / '.config/Nextcloud/nextcloud.cfg').write_text('[General]\n')
        output = io.StringIO()
        with mock.patch.object(Path, 'home', return_value=self.home), \
                mock.patch.object(nextcloud, 'bus', side_effect=AssertionError('no D-Bus call expected')), \
                redirect_stdout(output):
            self.assertEqual(nextcloud.main([]), 0)
        self.assertIn('no folders configured', output.getvalue())


if __name__ == '__main__':
    unittest.main()
