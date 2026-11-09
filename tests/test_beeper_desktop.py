"""The Beeper Desktop installer trusts only its channel's update feed."""

import base64
import hashlib
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = runpy.run_path(str(ROOT / 'scripts/install-beeper-desktop.py'))
LAUNCHER = ROOT / 'home/private_dot_local/private_share/applications/create_Beeper.desktop.tmpl'

PAYLOAD = b'appimage'
DIGEST = base64.b64encode(hashlib.sha512(PAYLOAD).digest()).decode()


SIZE = len(PAYLOAD)


def manifest(url='https://beeper-desktop.download.beeper.com/builds/Beeper-9.9.9-x86_64.AppImage', size=SIZE):
    return f'version: 9.9.9\nfiles:\n  - url: {url}\n    sha512: {DIGEST}\n    size: {size}\n'


class Response:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        data, self.data = self.data, b''
        return data


class BeeperDesktopTests(unittest.TestCase):
    def test_the_feed_names_the_appimage_its_size_and_digest(self):
        version, url, size, digest = INSTALLER['release'](manifest())
        self.assertEqual(version, '9.9.9')
        self.assertEqual(url, 'https://beeper-desktop.download.beeper.com/builds/Beeper-9.9.9-x86_64.AppImage')
        self.assertEqual((size, digest), (len(PAYLOAD), hashlib.sha512(PAYLOAD).digest()))
        feed = urlsplit(INSTALLER['manifest_url']('x86_64'))
        self.assertEqual(
            feed._replace(query='').geturl(), 'https://api.beeper.com/desktop/update-feed/latest-linux.yml'
        )
        self.assertEqual(parse_qs(feed.query)['channel'], ['stable'])
        with self.assertRaisesRegex(SystemExit, 'no Linux build'):
            INSTALLER['manifest_url']('aarch64')

    def test_a_feed_pointing_elsewhere_is_refused(self):
        for url in (
            'https://elsewhere.example.invalid/Beeper.AppImage',
            'http://beeper-desktop.download.beeper.com/builds/Beeper.AppImage',
        ):
            with self.subTest(url=url), self.assertRaisesRegex(SystemExit, 'outside'):
                INSTALLER['release'](manifest(url=url))
        with self.assertRaisesRegex(SystemExit, 'no AppImage'):
            INSTALLER['release']('version: 1\nfiles: []\n')

    def test_a_download_that_differs_from_the_feed_is_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'Beeper.AppImage.partial'
            url = 'https://beeper-desktop.download.beeper.com/builds/x.AppImage'
            with (
                patch.object(INSTALLER['urllib'].request, 'urlopen', return_value=Response(b'tampered!')),
                self.assertRaisesRegex(SystemExit, 'does not match'),
            ):
                INSTALLER['download'](url, destination, len(PAYLOAD), hashlib.sha512(PAYLOAD).digest())
            self.assertFalse(destination.exists())
            with patch.object(INSTALLER['urllib'].request, 'urlopen', return_value=Response(PAYLOAD)) as fetched:
                INSTALLER['download'](url, destination, len(PAYLOAD), hashlib.sha512(PAYLOAD).digest())
            self.assertEqual(destination.read_bytes(), PAYLOAD)
            # The download server refuses Python's default user agent.
            self.assertEqual(fetched.call_args[0][0].get_header('User-agent'), INSTALLER['USER_AGENT'])

    def test_an_installed_copy_is_left_to_its_own_updater(self):
        with tempfile.TemporaryDirectory() as home:
            target = Path(home) / INSTALLER['TARGET']
            target.parent.mkdir(parents=True)
            target.write_bytes(b'installed')
            with (
                patch.dict('os.environ', {'HOME': home}),
                patch.object(INSTALLER['urllib'].request, 'urlopen', side_effect=AssertionError('no download')),
            ):
                INSTALLER['main']()
            self.assertEqual(target.read_bytes(), b'installed')

    def test_the_launcher_is_created_once_under_the_name_beeper_rewrites(self):
        # Beeper writes ~/.local/share/applications/Beeper.desktop at every start; a managed
        # file of that name would show drift, and one of another name a second entry.
        self.assertTrue(LAUNCHER.name.startswith('create_'))
        self.assertEqual(LAUNCHER.name.removeprefix('create_').removesuffix('.tmpl'), 'Beeper.desktop')
        self.assertNotIn('no-sandbox', LAUNCHER.read_text())
        self.assertIn(str(INSTALLER['TARGET']), LAUNCHER.read_text())


if __name__ == '__main__':
    unittest.main()
