"""Keys that Noctalia's runtime files override are reported."""

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy.noctalia import overlaps


class OverlapTests(unittest.TestCase):
    def test_keys_set_by_fragments_and_runtime_files_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rendered = root / 'rendered'
            rendered.mkdir()
            (rendered / '90-base.toml').write_text('[theme]\nmode = "light"\n[bar]\nposition = "top"\n')
            (rendered / '97-supplement.toml').write_text('[bar]\nposition = "bottom"\n')
            home = root / 'home'
            (home / '.config/noctalia').mkdir(parents=True)
            (home / '.local/state/noctalia').mkdir(parents=True)
            (home / '.config/noctalia/99-runtime.toml').write_text('[bar]\nposition = "bottom"\n')
            (home / '.local/state/noctalia/settings.toml').write_text('[theme]\nmode = "dark"\n[unrelated]\nkey = 1\n')
            found = {
                (key, source.split('/')[-1]): (managed, runtime)
                for key, managed, source, runtime in overlaps(rendered, home)
            }
            self.assertEqual(
                found,
                {
                    ('bar.position', '99-runtime.toml'): ('bottom', 'bottom'),
                    ('theme.mode', 'settings.toml'): ('light', 'dark'),
                },
            )

    def test_missing_runtime_files_mean_no_overlaps(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / '90-base.toml').write_text('[theme]\nmode = "light"\n')
            self.assertEqual(overlaps(directory, directory), [])


if __name__ == '__main__':
    unittest.main()
