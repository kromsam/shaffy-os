"""The Yaru installer replaces its own older install and leaves anyone else's theme alone."""
import hashlib
import io
from pathlib import Path
import runpy
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class YaruInstallerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.home = self.tmp / 'home'
        self.installer = runpy.run_path(str(ROOT / 'scripts/install-yaru-icons.py'))
        version = self.installer['VERSION']
        self.archive = self.tmp / 'yaru.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name in ('Yaru', 'Yaru-yellow'):
                data = f'{name} {version}'.encode()
                info = tarfile.TarInfo(f'yaru-{version}/icons/{name}/icon.svg')
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        self.installer['main'].__globals__['SHA256'] = hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def install(self):
        with mock.patch.dict('os.environ', {'HOME': str(self.home)}), \
                mock.patch('sys.argv', ['install-yaru-icons.py', str(self.archive)]), \
                mock.patch.object(self.installer['subprocess'], 'run') as run, \
                mock.patch('sys.stdout', new_callable=io.StringIO):
            self.installer['main']()
        return run

    def theme(self, name):
        return self.home / '.local/share/icons' / name

    def test_an_older_install_of_ours_is_replaced(self):
        for name in ('Yaru', 'Yaru-yellow'):
            self.theme(name).mkdir(parents=True)
            (self.theme(name) / '.shaffy-version').write_text('1.0ubuntu\n')
            (self.theme(name) / 'stale.svg').write_text('old')
        run = self.install()
        version = self.installer['VERSION']
        for name in ('Yaru', 'Yaru-yellow'):
            self.assertEqual((self.theme(name) / '.shaffy-version').read_text(), version + '\n')
            self.assertEqual((self.theme(name) / 'icon.svg').read_text(), f'{name} {version}')
            self.assertFalse((self.theme(name) / 'stale.svg').exists())
        self.assertEqual(sorted(p.name for p in self.theme('Yaru').parent.iterdir()), ['Yaru', 'Yaru-yellow'])
        self.assertEqual(run.call_count, 4)  # the index generator and the icon cache, per theme
        self.assertEqual(self.install().call_count, 0)  # the pinned release is already in place

    def test_a_theme_that_is_not_ours_is_left_alone(self):
        self.theme('Yaru').mkdir(parents=True)
        (self.theme('Yaru') / 'mine.svg').write_text('mine')
        with self.assertRaisesRegex(SystemExit, 'inspect it before replacing'):
            self.install()
        self.assertEqual((self.theme('Yaru') / 'mine.svg').read_text(), 'mine')


if __name__ == '__main__':
    unittest.main()
