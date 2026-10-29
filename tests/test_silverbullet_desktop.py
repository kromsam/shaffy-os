"""The SilverBullet Desktop installer trusts only its channel's manifest."""

import base64
import hashlib
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = runpy.run_path(str(ROOT / 'scripts/install-silverbullet-desktop.py'))

PAYLOAD = b'appimage'
DIGEST = base64.b64encode(hashlib.sha512(PAYLOAD).digest()).decode()


def manifest(url='/electron/artifacts/9.9.9/linux/x64/SilverBullet-9.9.9-linux-x86_64.AppImage', size=len(PAYLOAD)):
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


class SilverBulletDesktopTests(unittest.TestCase):
    def test_the_manifest_names_the_appimage_its_size_and_digest(self):
        version, url, size, digest = INSTALLER['release'](manifest())
        self.assertEqual(version, '9.9.9')
        self.assertEqual(
            url,
            'https://release.silverbullet.md/electron/artifacts/9.9.9/linux/x64/'
            'SilverBullet-9.9.9-linux-x86_64.AppImage',
        )
        self.assertEqual((size, digest), (len(PAYLOAD), hashlib.sha512(PAYLOAD).digest()))
        self.assertEqual(
            INSTALLER['manifest_url']('x86_64'),
            'https://release.silverbullet.md/electron/stable/linux/x64/latest-linux.yml',
        )

    def test_a_manifest_pointing_elsewhere_is_refused(self):
        with self.assertRaisesRegex(SystemExit, 'outside'):
            INSTALLER['release'](manifest(url='https://elsewhere.example.invalid/SilverBullet.AppImage'))
        with self.assertRaisesRegex(SystemExit, 'no AppImage'):
            INSTALLER['release']('version: 1\nfiles: []\n')

    def test_a_download_that_differs_from_the_manifest_is_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'SilverBullet.AppImage.partial'
            urlopen = INSTALLER['urllib'].request.urlopen
            with patch.object(INSTALLER['urllib'].request, 'urlopen', return_value=Response(b'tampered!')):
                with self.assertRaisesRegex(SystemExit, 'does not match'):
                    INSTALLER['download'](
                        'https://release.silverbullet.md/x.AppImage',
                        destination,
                        len(PAYLOAD),
                        hashlib.sha512(PAYLOAD).digest(),
                    )
            self.assertFalse(destination.exists())
            with patch.object(INSTALLER['urllib'].request, 'urlopen', return_value=Response(PAYLOAD)) as fetched:
                INSTALLER['download'](
                    'https://release.silverbullet.md/x.AppImage',
                    destination,
                    len(PAYLOAD),
                    hashlib.sha512(PAYLOAD).digest(),
                )
            self.assertEqual(destination.read_bytes(), PAYLOAD)
            # The release server refuses Python's default user agent.
            self.assertEqual(fetched.call_args[0][0].get_header('User-agent'), INSTALLER['USER_AGENT'])
            self.assertIs(INSTALLER['urllib'].request.urlopen, urlopen)

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


if __name__ == '__main__':
    unittest.main()
