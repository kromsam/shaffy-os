"""The desktop key helper is built outside the checkout and installed atomically."""

import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy import desktop_key


class ProblemTests(unittest.TestCase):
    def test_missing_tools_are_named_with_their_packages(self):
        with mock.patch.object(desktop_key.shutil, 'which', return_value=None):
            problems = desktop_key.problems()
        self.assertEqual(len(problems), 1)
        for name in ('cc (package gcc)', 'wayland-scanner (package wayland)', 'pkg-config (package pkgconf)'):
            self.assertIn(name, problems[0])


@unittest.skipIf(desktop_key.problems(), 'the desktop key build tools are not installed')
class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.tmp = Path(self.temporary.name)
        patcher = mock.patch.dict(os.environ, {'XDG_CACHE_HOME': str(self.tmp / 'cache')})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_build_writes_only_the_build_cache(self):
        before = sorted(p.name for p in ROOT.iterdir())
        binary = desktop_key.build(ROOT)
        self.assertEqual(binary, self.tmp / 'cache/shaffy/build/desktop-key/shaffy-desktop-key')
        self.assertEqual(binary.read_bytes()[:4], b'\x7fELF')
        self.assertEqual(sorted(p.name for p in ROOT.iterdir()), before)

    def test_install_replaces_and_reports_differences(self):
        binary = desktop_key.build(ROOT)
        home = self.tmp / 'home'
        self.assertTrue(desktop_key.differs(binary, home))
        desktop_key.install(binary, home)
        installed = home / desktop_key.TARGET
        self.assertEqual(installed.stat().st_mode & 0o777, 0o755)
        self.assertFalse(desktop_key.differs(binary, home))
        self.assertEqual([p.name for p in installed.parent.iterdir()], ['shaffy-desktop-key'])

    def test_a_compile_error_is_a_build_error(self):
        checkout = self.tmp / 'checkout'
        shutil.copytree(ROOT / 'files/desktop-key', checkout / 'files/desktop-key')
        with (checkout / 'files/desktop-key/desktop-key.c').open('a') as stream:
            stream.write('\nint broken(void) { return }\n')
        with self.assertRaisesRegex(desktop_key.BuildError, 'did not build'):
            desktop_key.build(checkout)


if __name__ == '__main__':
    unittest.main()
