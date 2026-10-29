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
from shaffy import capture as capture_module
from shaffy.capture import CaptureError, capture, verify
from shaffy.loader import load
from shaffy.selection import SelectionError, resolve


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

        with mock.patch.object(capture_module.shutil, 'copyfile', copy_then_edit):
            with self.assertRaisesRegex(CaptureError, 'changed while it was captured'):
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
        with self.marker('preview', 'op-6', manifest['directory'], 'apply'):
            with self.assertRaisesRegex(SelectionError, 'never applied'):
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
