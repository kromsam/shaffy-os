"""The integrations' configuration modifiers tolerate a fresh home."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
REAPER = ROOT / 'home/dot_config/private_REAPER/modify_reaper.ini'
ZETTLR = 'dot_config/private_Zettlr/modify_private_config.json.tmpl'


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


def render_zettlr(existing, zotero):
    """Zettlr's configuration rendered from EXISTING, that rendered again, and the home, with zotero in state ZOTERO."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        checkout, home = root / 'checkout', root / 'home'
        for name in ('lib', 'schema', 'defaults'):
            shutil.copytree(ROOT / name, checkout / name, ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('.chezmoitemplates/shaffy.json', ZETTLR):
            (checkout / 'home' / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / 'home' / name, checkout / 'home' / name)
        (root / 'supplement').mkdir()
        (root / 'supplement/config.yaml').write_text(
            f'schema_version: 1\nsupplement_id: test\nintegrations:\n  zettlr: managed\n  zotero: {zotero}\n'
        )
        binding = home / '.local/state/shaffy/binding.json'
        binding.parent.mkdir(parents=True)
        binding.write_text(json.dumps({'binding': {'mode': 'supplement', 'path': str(root / 'supplement')}}))
        target = home / '.config/Zettlr/config.json'
        target.parent.mkdir(parents=True)
        (root / 'chezmoi.toml').write_text(f'persistentState = "{root / "state.boltdb"}"\n')
        rendered = []
        for text in (existing, None):
            target.write_text(rendered[-1] if text is None else text)
            result = subprocess.run(
                ['chezmoi', '--config', str(root / 'chezmoi.toml'), '--source', str(checkout / 'home')]
                + ['--destination', str(home), 'cat', str(target)],
                capture_output=True,
                text=True,
                cwd=root,
                env={'HOME': str(home), 'PATH': os.environ['PATH']},
                check=False,
            )
            if result.returncode:
                raise AssertionError(result.stderr)
            rendered.append(result.stdout)
        return *rendered, home


@unittest.skipUnless(shutil.which('chezmoi'), 'chezmoi is not installed')
class ZettlrTests(unittest.TestCase):
    def test_before_zettlr_runs_nothing_is_created(self):
        self.assertEqual(render_zettlr('', 'managed')[0], '')

    def test_the_citation_library_is_better_bibtexs_export_only_with_zotero_managed(self):
        existing = json.dumps({'export': {'useBundledPandoc': True, 'cslLibrary': ''}, 'mine': 1})
        for zotero in ('managed', 'disabled'):
            with self.subTest(zotero=zotero):
                text, again, home = render_zettlr(existing, zotero)
                result = json.loads(text)
                self.assertEqual(
                    result['export']['cslLibrary'],
                    f'{home}/.local/share/zotero-library/My Library.json' if zotero == 'managed' else '',
                )
                self.assertEqual(result['export']['useBundledPandoc'], False)
                self.assertEqual(result['display']['hideToolbarInDistractionFree'], True)
                self.assertEqual(result['mine'], 1)
                self.assertEqual(again, text)


if __name__ == '__main__':
    unittest.main()
