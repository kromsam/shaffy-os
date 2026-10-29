"""Installer-owned XDG directories and selective overrides."""

import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy.folders import folder_path, merge_user_dirs


class OverrideTests(unittest.TestCase):
    def test_only_named_entries_change_and_other_bytes_survive(self):
        original = (
            '# Created by the installation\nXDG_DOCUMENTS_DIR="$HOME/Dokumente"\n'
            'XDG_PICTURES_DIR="$HOME/Bilder"\n\n# Disabled\nXDG_MUSIC_DIR="$HOME/"\n'
        )
        expected = original.replace('$HOME/Bilder', '$HOME/Photos')
        actual = merge_user_dirs(original, {'pictures': 'Photos'})
        self.assertEqual(actual, expected)
        self.assertEqual(merge_user_dirs(actual, {'pictures': 'Photos'}), expected)

    def test_no_standard_override_leaves_even_an_empty_file_alone(self):
        for text in ('', '# untouched\nXDG_PROJECTS_DIR="$HOME/Code"'):
            self.assertEqual(merge_user_dirs(text, {}), text)

    def test_a_missing_entry_is_added_without_losing_the_last_line(self):
        self.assertEqual(
            merge_user_dirs('# Keep this', {'pictures': 'Photos'}), '# Keep this\nXDG_PICTURES_DIR="$HOME/Photos"\n'
        )

    def test_an_already_correct_entry_keeps_its_spacing_and_comment(self):
        original = ' XDG_PICTURES_DIR = "$HOME/Photos"  # Keep this\n'
        self.assertEqual(merge_user_dirs(original, {'pictures': 'Photos'}), original)

    def test_an_already_correct_absolute_path_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as home:
            original = f'XDG_PICTURES_DIR="{home}/Photos"\n'
            self.assertEqual(merge_user_dirs(original, {'pictures': 'Photos'}, home), original)


@unittest.skipUnless(shutil.which('xdg-user-dir'), 'needs xdg-user-dirs')
class PlatformDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        (self.home / '.config').mkdir()
        self.dirs = self.home / '.config/user-dirs.dirs'

    def test_native_lookup_handles_unicode_escaped_names_and_absolute_paths(self):
        self.dirs.write_text(
            'XDG_DOCUMENTS_DIR="$HOME/書類"\n'
            'XDG_PICTURES_DIR="$HOME/Photos \\"été\\" & 100%"\n'
            'XDG_VIDEOS_DIR="/srv/media/Films"\n'
            'XDG_MUSIC_DIR="$HOME/"\n'
        )
        expected = {
            'documents': self.home / '書類',
            'pictures': self.home / 'Photos "été" & 100%',
            'videos': Path('/srv/media/Films'),
            'music': self.home,
        }
        for role, path in expected.items():
            self.assertEqual(folder_path({}, self.home, role), path)

    def test_explicit_override_wins_before_the_platform_file_is_modified(self):
        self.dirs.write_text('XDG_PICTURES_DIR="$HOME/Bilder"\n')
        self.assertEqual(folder_path({'pictures': 'Photos'}, self.home, 'pictures'), self.home / 'Photos')
        self.assertIn('$HOME/Bilder', self.dirs.read_text())

    def test_platform_changes_are_seen_without_changing_supplement_settings(self):
        for name in ('Bilder', 'Images', '画像'):
            self.dirs.write_text(f'XDG_PICTURES_DIR="$HOME/{name}"\n')
            self.assertEqual(folder_path({}, self.home, 'pictures'), self.home / name)

    def test_lookup_does_not_execute_shell_text(self):
        marker = self.home / 'must-not-exist'
        self.dirs.write_text(f'XDG_PICTURES_DIR="$HOME/$(touch {marker})"\n')
        self.assertIn('$(touch ', str(folder_path({}, self.home, 'pictures')))
        self.assertFalse(marker.exists())

    def test_native_xdg_config_home_is_respected(self):
        config = self.home / 'config'
        config.mkdir()
        (config / 'user-dirs.dirs').write_text('XDG_PICTURES_DIR="$HOME/Images"\n')
        with mock.patch.dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(config)):
            self.assertEqual(folder_path({}, self.home, 'pictures'), self.home / 'Images')
