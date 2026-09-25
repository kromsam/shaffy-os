"""Behavioral tests for cancellation, file safety and reversible desktop actions."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'home/private_dot_local/bin/executable_shaffy-desktop'
loader = importlib.machinery.SourceFileLoader('desktop_controls', str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
desktop = importlib.util.module_from_spec(spec)
loader.exec_module(desktop)


class DesktopControlsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.root = root
        for name, value in [('STATE', root / 'state/shaffy'), ('CONFIG', root / 'config')]:
            patcher = patch.object(desktop, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_unrecognized_menu_output_is_not_executed(self):
        with patch.object(desktop, 'choose', return_value='$(touch /tmp/unwanted)'), patch.object(desktop, 'run') as run:
            desktop.menu()
        run.assert_not_called()

    def test_cancelled_selection_does_not_capture_or_replace_clipboard(self):
        with patch.object(desktop.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', '')) as run:
            desktop.capture_text('ocr')
        self.assertEqual([call.args[0] for call in run.call_args_list], [['slurp']])

    def test_ocr_copies_text_literally_without_shell_evaluation(self):
        calls = []
        text = "Private text; $(never execute)\n"
        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            output = '10,20 30x40\n' if args[0] == 'slurp' else text if args[0] == 'tesseract' else ''
            return subprocess.CompletedProcess(args, 0, output, '')
        with patch.object(desktop.subprocess, 'run', side_effect=fake_run), patch.object(desktop, 'notify') as notify:
            desktop.capture_text('ocr')
        copied = next(kwargs['input'] for args, kwargs in calls if args[0] == 'wl-copy')
        self.assertEqual(copied, text.rstrip('\n'))
        self.assertNotIn('Private text', str(notify.call_args_list))
        self.assertFalse(any(kwargs.get('shell') for _, kwargs in calls))

    def test_ocr_reads_every_installed_language_english_first(self):
        def listing(*names):
            output = 'List of available languages in "/usr/share/tessdata/" (%d):\n' % len(names) + '\n'.join(names)
            return subprocess.CompletedProcess([], 0, output + '\n', '')
        for names, expected in ((('deu', 'eng', 'osd'), 'eng+deu'), (('nld', 'osd'), 'nld'), (('osd',), 'eng')):
            with patch.object(desktop.subprocess, 'run', return_value=listing(*names)):
                self.assertEqual(desktop.ocr_languages(), expected)

    def test_square_toggle_restores_original_dimensions(self):
        window = {'id': 7, 'layout': {'window_size': [800, 600]}}
        with patch.object(desktop, 'query', return_value=window), patch.object(desktop, 'action') as action:
            desktop.square()
            desktop.square()
        self.assertEqual([c.args for c in action.call_args_list], [
            ('set-window-width', '--id', '7', '600'), ('set-window-height', '--id', '7', '600'),
            ('set-window-width', '--id', '7', '800'), ('set-window-height', '--id', '7', '600')])

    def test_gaps_toggle_keeps_generated_palette_separate(self):
        palette = desktop.CONFIG / 'niri/noctalia.kdl'
        palette.parent.mkdir(parents=True)
        palette.write_text('palette sentinel')
        desktop.gaps()
        self.assertIn('gaps 0', (palette.parent / 'omarchy-overrides.kdl').read_text())
        desktop.gaps()
        self.assertNotIn('gaps 0', (palette.parent / 'omarchy-overrides.kdl').read_text())
        self.assertEqual(palette.read_text(), 'palette sentinel')
        self.assertEqual((palette.parent / 'omarchy-overrides.kdl').stat().st_mode & 0o777, 0o644)
        self.assertEqual((desktop.STATE / 'gaps.json').stat().st_mode & 0o777, 0o600)

    def test_clock_and_battery_toggles_preserve_each_other(self):
        with patch.object(desktop, 'ipc'):
            desktop.shell_toggle('clock')
            desktop.shell_toggle('battery-label')
        data = tomllib.loads((desktop.CONFIG / 'noctalia/99-runtime.toml').read_text())
        self.assertEqual(data['widget']['clock']['format'], desktop.CLOCK_FORMATS[1])
        self.assertFalse(data['widget']['battery']['show_label'])

    def test_existing_conversion_is_never_overwritten(self):
        source = self.root / "source ' $(literal).png"
        source.write_bytes(b'original')
        destination = source.with_name(source.stem + '.converted.jpg')
        destination.write_bytes(b'previous conversion')
        selected = subprocess.CompletedProcess([], 0, str(source) + '\n', '')
        with patch.object(desktop.subprocess, 'run', return_value=selected) as run, patch.object(desktop, 'choose', return_value='JPEG image'), patch.object(desktop, 'notify'):
            desktop.transcode()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(source.read_bytes(), b'original')
        self.assertEqual(destination.read_bytes(), b'previous conversion')

    def test_failed_conversion_cleans_partial_output_preserves_original(self):
        source = self.root / 'source.png'
        source.write_bytes(b'original')
        selected = subprocess.CompletedProcess([], 0, str(source) + '\n', '')
        with patch.object(desktop.subprocess, 'run', side_effect=[selected, subprocess.CalledProcessError(1, ['ffmpeg'])]), patch.object(desktop, 'choose', return_value='JPEG image'), patch.object(desktop, 'notify'):
            with self.assertRaises(subprocess.CalledProcessError):
                desktop.transcode()
        self.assertEqual(source.read_bytes(), b'original')
        self.assertFalse((self.root / 'source.converted.jpg').exists())
        self.assertEqual(list(self.root.iterdir()), [source])


if __name__ == '__main__':
    unittest.main()
