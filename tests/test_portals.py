"""The portal preferences repeat Niri's own and change only the file chooser."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
OURS = ROOT / 'home/dot_config/xdg-desktop-portal/niri-portals.conf'
NIRI = Path('/usr/share/xdg-desktop-portal/niri-portals.conf')


def settings(path):
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith('#')]


class PortalTests(unittest.TestCase):
    def test_the_file_chooser_is_gtk(self):
        self.assertIn('org.freedesktop.impl.portal.FileChooser=gtk;', settings(OURS))

    @unittest.skipUnless(NIRI.exists(), 'Niri is not installed')
    def test_everything_else_is_niris_own(self):
        ours = [line for line in settings(OURS) if not line.startswith('org.freedesktop.impl.portal.FileChooser=')]
        self.assertEqual(ours, settings(NIRI))


if __name__ == '__main__':
    unittest.main()
