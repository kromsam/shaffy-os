"""The integrations' configuration modifiers tolerate a fresh home."""

import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
REAPER = ROOT / 'home/dot_config/private_REAPER/modify_reaper.ini'
ZETTLR = ROOT / 'home/dot_config/private_Zettlr/modify_private_config.json'


def run(modifier, text):
    return subprocess.run(
        ['/usr/bin/python3', str(modifier)], input=text, capture_output=True, text=True, check=True
    ).stdout


class ReaperTests(unittest.TestCase):
    def test_before_reaper_runs_nothing_is_created(self):
        self.assertEqual(run(REAPER, ''), '')

    def test_audio_goes_through_pipewire_and_the_rest_is_kept(self):
        text = '[REAPER]\nalsa_indev=hw:Device\nprojectpath=/somewhere\nlinux_audio_mode=0\n[other]\nx=1\n'
        result = run(REAPER, text)
        for line in (
            'alsa_indev=pipewire',
            'alsa_outdev=pipewire',
            'linux_audio_mode=1',
            'linux_auto_pasuspend=0',
            'projectpath=/somewhere',
            '[other]\nx=1',
        ):
            self.assertIn(line, result)
        self.assertEqual(run(REAPER, result), result)

    def test_a_first_run_file_with_a_lowercase_section_is_configured(self):
        # The shape REAPER 7.80 writes when it first closes (seen in the VM).
        text = '[.swell]\nui_scale=1.0\n\n[nag]\nnag=X\n\n[reaper]\nlastproject=\nwnd_w=608\n\n[verchk]\nlastt=1\n'
        result = run(REAPER, text)
        section = result.split('[reaper]\n', 1)[1].split('\n[verchk]', 1)[0]
        for line in (
            'alsa_indev=pipewire',
            'alsa_outdev=pipewire',
            'linux_audio_mode=1',
            'linux_auto_pasuspend=0',
            'wnd_w=608',
        ):
            self.assertIn(line, section.splitlines())
        self.assertNotIn('[REAPER]', result)
        self.assertEqual(run(REAPER, result), result)


class ZettlrTests(unittest.TestCase):
    def test_before_zettlr_runs_nothing_is_created(self):
        self.assertEqual(run(ZETTLR, ''), '')

    def test_packaged_pandoc_and_the_rest_is_kept(self):
        result = json.loads(run(ZETTLR, json.dumps({'export': {'useBundledPandoc': True}, 'mine': 1})))
        self.assertEqual(result['export']['useBundledPandoc'], False)
        self.assertEqual(result['display']['hideToolbarInDistractionFree'], True)
        self.assertEqual(result['mine'], 1)
        text = json.dumps(result, indent=2)
        self.assertEqual(run(ZETTLR, text), text)


if __name__ == '__main__':
    unittest.main()
