"""Frozen supplement inputs and the operation marker."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shaffy import capture as capture_module
from shaffy.capture import CaptureError, capture, verify
from shaffy.loader import load
from shaffy.selection import SelectionError, resolve
from support import vendor_profile


class _Case(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.tmp = Path(self.temporary.name)
        self.supplement = self.tmp / 'supplement'
        shutil.copytree(ROOT / 'tests/fixtures/supplements/comprehensive', self.supplement)
        self.selection = {'mode': 'supplement', 'path': str(self.supplement), 'source': 'explicit'}


class CaptureTests(_Case):
    def test_capture_copies_privately_and_records_digests(self):
        manifest = capture(ROOT, self.selection, self.tmp / 'state', 'op-1', load)
        directory = Path(manifest['directory'])
        self.assertEqual(
            (directory / 'supplement/config.yaml').read_bytes(), (self.supplement / 'config.yaml').read_bytes()
        )
        self.assertEqual(oct(directory.stat().st_mode & 0o777), '0o700')
        self.assertEqual(oct((directory / 'manifest.json').stat().st_mode & 0o777), '0o600')
        self.assertEqual(manifest['supplement_id'], 'synthetic-desktop')
        self.assertEqual(verify(directory)['files'], manifest['files'])

    def test_an_edit_during_capture_is_refused(self):
        original = shutil.copyfile

        def copy_then_edit(source, destination):
            result = original(source, destination)
            Path(source).write_text(Path(source).read_text() + '# edited meanwhile\n')
            return result

        with (
            mock.patch.object(capture_module.shutil, 'copyfile', copy_then_edit),
            self.assertRaisesRegex(CaptureError, 'changed while it was captured'),
        ):
            capture(ROOT, self.selection, self.tmp / 'state', 'op-2', load)
        # No copy of the private settings is left behind without a record.
        self.assertFalse((self.tmp / 'state/runs/op-2').exists())

    def test_edits_after_capture_do_not_reach_the_captured_settings(self):
        manifest = capture(ROOT, self.selection, self.tmp / 'state', 'op-3', load)
        (self.supplement / 'config.yaml').write_text('schema_version: 1\nsupplement_id: other\n')
        captured = load(ROOT, supplement=Path(manifest['directory']) / 'supplement')
        self.assertEqual(captured['shaffy']['selection']['supplement_id'], 'synthetic-desktop')

    def test_override_files_are_captured_and_frozen(self):
        manifest = capture(ROOT, self.selection, self.tmp / 'state', 'op-7', load)
        self.assertIn('supplement/overrides/fish.fish', manifest['files'])
        (self.supplement / 'overrides/fish.fish').write_text('echo changed later\n')
        captured = load(ROOT, supplement=Path(manifest['directory']) / 'supplement')
        self.assertIn('synthetic-supplement-greeting', Path(captured['shaffy']['overrides']['fish']).read_text())

    def test_references_through_symlinks_are_captured_under_their_written_paths(self):
        hosts = self.supplement / 'hosts'
        hosts.mkdir()
        (self.supplement / 'overrides/niri.kdl').rename(hosts / 'niri.kdl')
        (self.supplement / 'overrides/niri.kdl').symlink_to('../hosts/niri.kdl')
        (self.supplement / 'shared').symlink_to('hosts')
        (self.supplement / 'overrides/fish.fish').rename(hosts / 'fish.fish')
        config = self.supplement / 'config.yaml'
        config.write_text(config.read_text().replace('fish: overrides/fish.fish', 'fish: shared/fish.fish'))
        manifest = capture(ROOT, self.selection, self.tmp / 'state', 'op-11', load)
        captured = load(ROOT, supplement=Path(manifest['directory']) / 'supplement')['shaffy']
        self.assertIn('supplement/overrides/niri.kdl', manifest['files'])
        self.assertIn('supplement/shared/fish.fish', manifest['files'])
        for name, source in (('niri', hosts / 'niri.kdl'), ('fish', hosts / 'fish.fish')):
            self.assertEqual(Path(captured['overrides'][name]).read_text(), source.read_text())
        verify(manifest['directory'])

    def test_a_tampered_capture_fails_verification(self):
        manifest = capture(ROOT, self.selection, self.tmp / 'state', 'op-4', load)
        with open(Path(manifest['directory']) / 'supplement/config.yaml', 'a') as stream:
            stream.write('# tampered\n')
        with self.assertRaisesRegex(CaptureError, 'no longer matches'):
            verify(manifest['directory'])

    def test_the_selection_is_part_of_the_manifest(self):
        manifest = capture(ROOT, self.selection, self.tmp / 'state', 'op-8', load)
        self.assertIn('selection.json', manifest['files'])
        selection = Path(manifest['directory']) / 'selection.json'
        selection.write_text(json.dumps({'mode': 'none', 'path': None, 'source': 'explicit'}))
        with self.assertRaisesRegex(CaptureError, 'no longer matches'):
            verify(manifest['directory'])

    def test_a_defaults_only_capture_is_verified_too(self):
        selection = {'mode': 'none', 'path': None, 'source': 'explicit'}
        manifest = capture(ROOT, selection, self.tmp / 'state', 'op-9', load)
        self.assertEqual(sorted(manifest['files']), ['selection.json', 'settings.json'])
        verify(manifest['directory'])
        (Path(manifest['directory']) / 'selection.json').write_text(json.dumps(self.selection))
        with self.assertRaises(CaptureError):
            verify(manifest['directory'])

    def test_an_extra_file_fails_verification(self):
        manifest = capture(ROOT, self.selection, self.tmp / 'state', 'op-10', load)
        (Path(manifest['directory']) / 'supplement/extra.yaml').write_text('x: 1\n')
        with self.assertRaisesRegex(CaptureError, 'other files'):
            verify(manifest['directory'])

    def test_a_missing_capture_fails_clearly(self):
        with self.assertRaisesRegex(CaptureError, 'missing or unreadable'):
            verify(self.tmp / 'state/runs/op-gone/capture')


class DotfilesCaptureTests(_Case):
    """The supplement's dotfiles folder, captured file by file beside the referenced files."""

    def test_a_system_capture_holds_no_dotfiles_and_ignores_a_broken_folder(self):
        (self.tmp / 'outside.txt').write_text('secret\n')
        (self.supplement / 'dotfiles/dot_outside').symlink_to(self.tmp / 'outside.txt')
        manifest = capture(ROOT, self.selection, self.tmp / 'state', 'op-s', load)
        self.assertEqual([name for name in manifest['files'] if name.startswith('supplement/dotfiles')], [])
        self.assertEqual(list((Path(manifest['directory']) / 'supplement/dotfiles').iterdir()), [])
        verify(manifest['directory'])
        self.refused('leads outside the supplement', op='op-h')

    def dotfiles(self, files):
        shutil.rmtree(self.supplement / 'dotfiles')  # the fixture's own tree, replaced by FILES
        for name, content in files.items():
            (self.supplement / 'dotfiles' / name).parent.mkdir(parents=True, exist_ok=True)
            (self.supplement / 'dotfiles' / name).write_text(content)
        return self.supplement / 'dotfiles'

    def refused(self, pattern, op='op-d'):
        with self.assertRaisesRegex(CaptureError, pattern):
            capture(ROOT, self.selection, self.tmp / 'state', op, load, dotfiles=True)
        self.assertFalse((self.tmp / f'state/runs/{op}').exists())

    def test_the_tree_is_captured_under_its_written_path_and_listed(self):
        files = {'dot_config/app/settings.json': '{}\n', '.chezmoiignore': 'README.md\n', 'README.md': '# mine\n'}
        self.dotfiles(files)
        manifest = capture(ROOT, self.selection, self.tmp / 'state', 'op-d1', load, dotfiles=True)
        directory = Path(manifest['directory'])
        for name, content in files.items():
            self.assertIn(f'supplement/dotfiles/{name}', manifest['files'])
            self.assertEqual((directory / 'supplement/dotfiles' / name).read_text(), content)
        self.assertEqual(load(ROOT, supplement=directory / 'supplement')['shaffy']['dotfiles'], 'dotfiles')
        verify(directory)

    def test_git_is_left_out(self):
        root = self.dotfiles({'dot_bashrc': 'x\n', 'vendor/.git/config': '[core]\n', 'vendor/dot_x': 'x\n'})
        (root / '.git').write_text('gitdir: ../.git/modules/dotfiles\n')  # a submodule's file
        manifest = capture(ROOT, self.selection, self.tmp / 'state', 'op-d2', load, dotfiles=True)
        captured = [name for name in manifest['files'] if name.startswith('supplement/dotfiles/')]
        self.assertEqual(sorted(captured), ['supplement/dotfiles/dot_bashrc', 'supplement/dotfiles/vendor/dot_x'])

    def test_a_link_to_a_file_in_the_supplement_is_captured_as_its_content(self):
        root = self.dotfiles({'dot_bashrc': 'x\n'})
        (self.supplement / 'shared').mkdir()
        (self.supplement / 'shared/gitconfig').write_text('[user]\n')
        (root / 'dot_gitconfig').symlink_to('../shared/gitconfig')
        manifest = capture(ROOT, self.selection, self.tmp / 'state', 'op-d3', load, dotfiles=True)
        copy = Path(manifest['directory']) / 'supplement/dotfiles/dot_gitconfig'
        self.assertFalse(copy.is_symlink())
        self.assertEqual(copy.read_text(), '[user]\n')

    def test_links_out_of_the_supplement_or_to_a_folder_are_refused(self):
        root = self.dotfiles({'dot_bashrc': 'x\n', 'dot_config/app/a.conf': 'a\n'})
        (self.tmp / 'outside.txt').write_text('secret\n')
        for link, target, reason in (
            ('dot_outside', self.tmp / 'outside.txt', 'leads outside the supplement'),
            ('dot_folder', 'dot_config', 'is neither a regular file nor a link to one'),
            ('dot_broken', 'absent', 'is neither a regular file nor a link to one'),
        ):
            with self.subTest(link=link):
                (root / link).symlink_to(target)
                self.refused(f'{link} {reason}')
                (root / link).unlink()

    def test_an_empty_tree_is_refused_naming_the_submodule_command(self):
        root = self.dotfiles({})
        root.mkdir()
        (root / '.git').write_text('gitdir: ../.git/modules/dotfiles\n')
        self.refused('git submodule update --init')

    def test_both_limits_are_refused(self):
        self.dotfiles({'dot_a': 'a\n', 'dot_b': 'b\n', 'dot_c': 'c\n'})
        with mock.patch.object(capture_module, 'TREE_FILES', 2):
            self.refused('more than 2 files')
        (self.supplement / 'dotfiles/dot_big').write_bytes(b'x' * (2**20 + 1))
        with mock.patch.object(capture_module, 'TREE_BYTES', 2**20):
            self.refused('more than 1 MiB')

    def test_an_edit_or_a_new_file_during_the_capture_is_refused(self):
        root = self.dotfiles({'dot_bashrc': 'x\n', 'dot_profile': 'y\n'})
        original = shutil.copyfile
        for change in (
            lambda: (root / 'dot_bashrc').write_text('edited\n'),
            lambda: (root / 'dot_new').write_text('new\n'),
        ):
            with self.subTest(change=change):

                def copy_then_change(source, destination, change=change):
                    result = original(source, destination)
                    if Path(source).name == 'dot_profile':
                        change()
                    return result

                with mock.patch.object(capture_module.shutil, 'copyfile', copy_then_change):
                    self.refused('changed while it was captured')
                (root / 'dot_bashrc').write_text('x\n')
                (root / 'dot_new').unlink(missing_ok=True)

    def test_verify_finds_an_added_or_changed_file_in_the_tree(self):
        self.dotfiles({'dot_bashrc': 'x\n'})
        for op, change, reason in (
            ('op-d8', lambda tree: (tree / 'dot_added').write_text('a\n'), 'other files'),
            ('op-d9', lambda tree: (tree / 'dot_bashrc').write_text('changed\n'), 'no longer matches'),
        ):
            with self.subTest(reason=reason):
                manifest = capture(ROOT, self.selection, self.tmp / 'state', op, load, dotfiles=True)
                change(Path(manifest['directory']) / 'supplement/dotfiles')
                with self.assertRaisesRegex(CaptureError, reason):
                    verify(manifest['directory'])


class ProfileCaptureTests(_Case):
    """A listed profile's files travel through reference_paths, so the capture needs no change."""

    def setUp(self):
        super().setUp()
        manifest = (
            'schema_version: 1\ndescription: x\noverrides:\n  fish: a.fish\n'
            'assets:\n  "~/.local/share/example/x": x.txt\n'
        )
        vendor_profile(
            self.supplement, 'alice/notes', {'profile.yaml': manifest, 'a.fish': '# alice\n', 'x.txt': 'x\n'}
        )
        vendor_profile(self.supplement, 'bob/notes', {'profile.yaml': 'schema_version: 1\ndescription: y\n'})
        with open(self.supplement / 'config.yaml', 'a') as stream:
            stream.write('profiles:\n  - alice/notes\n')

    def test_a_capture_holds_the_lock_and_the_listed_profiles_files_only(self):
        manifest = capture(ROOT, self.selection, self.tmp / 'state', 'op-p1', load)
        captured = sorted(name for name in manifest['files'] if name.startswith('supplement/profiles/'))
        self.assertEqual(
            captured,
            [
                'supplement/profiles/alice/notes/a.fish',
                'supplement/profiles/alice/notes/profile.yaml',
                'supplement/profiles/alice/notes/x.txt',
                'supplement/profiles/lock.json',
            ],
        )
        verify(manifest['directory'])

    def test_a_profile_file_changed_during_capture_is_refused(self):
        original = shutil.copyfile

        def copy_then_edit(source, destination):
            result = original(source, destination)
            if Path(source).name == 'a.fish':
                Path(source).write_text('# edited meanwhile\n')
            return result

        with (
            mock.patch.object(capture_module.shutil, 'copyfile', copy_then_edit),
            self.assertRaisesRegex(CaptureError, 'changed while it was captured'),
        ):
            capture(ROOT, self.selection, self.tmp / 'state', 'op-p2', load)
        self.assertFalse((self.tmp / 'state/runs/op-p2').exists())

    def test_settings_from_the_capture_equal_the_live_ones_with_profiles(self):
        live = load(ROOT, supplement=self.supplement)['shaffy']
        manifest = capture(ROOT, self.selection, self.tmp / 'state', 'op-p3', load)
        (self.supplement / 'profiles/alice/notes/a.fish').write_text('# edited later\n')
        captured = load(ROOT, supplement=Path(manifest['directory']) / 'supplement')['shaffy']
        self.assertEqual(captured['settings'], live['settings'])
        self.assertEqual(captured['profiles'], live['profiles'])
        self.assertEqual(
            [(a['target'], a['sha256'], a['layer']) for a in captured['assets']],
            [(a['target'], a['sha256'], a['layer']) for a in live['assets']],
        )
        fragment = next(a for a in captured['assets'] if a.get('fragment') == 'fish')
        self.assertEqual(Path(fragment['path']).read_text(), '# alice\n')


class MarkerTests(_Case):
    def marker(self, mode, op, directory, command='diff'):
        return mock.patch.dict(
            os.environ,
            {'SHAFFY_OP': op, 'SHAFFY_OP_MODE': mode, 'SHAFFY_OP_CAPTURE': str(directory), 'CHEZMOI_COMMAND': command},
        )

    def test_apply_marker_needs_the_matching_unfinished_operation(self):
        home = self.tmp / 'home'
        state = home / '.local/state/shaffy'
        manifest = capture(ROOT, self.selection, state, 'op-5', load)
        with self.marker('apply', 'op-5', manifest['directory'], 'apply'):
            with self.assertRaisesRegex(SelectionError, 'not the unfinished operation'):
                resolve(home=home)
            (state / 'binding.json').write_text(
                json.dumps({'record_version': 1, 'pending': {'op': 'op-5', 'capture': manifest['directory']}})
            )
            selection = resolve(home=home)
        self.assertEqual(selection['source'], 'capture')
        self.assertEqual(selection['path'], str(Path(manifest['directory']) / 'supplement'))

    def test_preview_marker_is_read_only(self):
        manifest = capture(ROOT, self.selection, self.tmp / 'state', 'op-6', load)
        with self.marker('preview', 'op-6', manifest['directory'], 'diff'):
            self.assertEqual(resolve(home=self.tmp)['source'], 'capture')
        with (
            self.marker('preview', 'op-6', manifest['directory'], 'apply'),
            self.assertRaisesRegex(SelectionError, 'never applied'),
        ):
            resolve(home=self.tmp)


@unittest.skipUnless(shutil.which('chezmoi'), 'needs chezmoi')
class PreviewCommandTests(unittest.TestCase):
    def test_diff_with_another_supplement_previews_from_a_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            home.mkdir()
            env = {'HOME': str(home), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
            result = subprocess.run(
                [
                    str(ROOT / 'bin/shaffy'),
                    'diff',
                    '--supplement',
                    str(ROOT / 'tests/fixtures/supplements/comprehensive'),
                    str(home / '.codex/config.toml'),
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('[mcp_servers.notes]', result.stdout)
            self.assertIn('from capture', result.stderr)
            state = home / '.local/state/shaffy'
            self.assertEqual(json.loads((state / 'journal.jsonl').read_text())['event'], 'preview')
            self.assertFalse((state / 'binding.json').exists())  # a preview binds nothing
            self.assertFalse((home / '.codex').exists())  # and applies nothing
            self.assertEqual(list((state / 'runs').iterdir()), [])  # its capture is removed


if __name__ == '__main__':
    unittest.main()
