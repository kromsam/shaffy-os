"""Integration check of managed presets, with synthetic content and bibliography."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import artifacts_dir, host_test  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'home/private_dot_local/private_share'
PRESETS = ROOT / 'home/dot_config/private_Zettlr/defaults'


def rendered(preset, work):
    """The preset as the home apply renders it with the public defaults."""
    config = work / 'chezmoi.toml'
    config.write_text(f'persistentState = "{work / "state.boltdb"}"\n')
    template = (PRESETS / (preset + '.tmpl')).read_text()
    result = subprocess.run(['chezmoi', '--config', str(config), '--source', str(ROOT / 'home'),
                             'execute-template'], input=template, capture_output=True, text=True,
                            env={'PATH': os.environ['PATH'], 'HOME': str(work), 'LANG': 'C.UTF-8'}, check=True)
    path = work / preset
    path.write_text(result.stdout)
    return path


@host_test('real Pandoc, LuaLaTeX and PDF tools',
           tools=('pandoc', 'lualatex', 'pdftotext', 'pdffonts', 'chezmoi'),
           paths=('/usr/share/texmf-dist/fonts/opentype/public/ebgaramond',))
class ExportTests(unittest.TestCase):
    def test_docx_and_pdf_preserve_text_citations_and_format_specific_filters(self):
        with tempfile.TemporaryDirectory(dir=artifacts_dir()) as temporary:
            work = Path(temporary)
            share = work / '.local/share'
            share.mkdir(parents=True)
            # The pandoc data as the home apply lays it out, with the style copy in csl/.
            (share / 'pandoc').mkdir()
            for part in ('filters', 'writing-header.tex'):
                (share / 'pandoc' / part).symlink_to(DATA / 'pandoc' / part)
            (share / 'pandoc/csl').symlink_to(DATA / 'zotero-data/styles', target_is_directory=True)
            config = work / 'fonts.conf'
            config.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">'
                              '<fontconfig><include>/etc/fonts/fonts.conf</include>'
                              '<dir>/usr/share/texmf-dist/fonts/opentype/public/ebgaramond</dir></fontconfig>')
            env = dict(os.environ, HOME=str(work), FONTCONFIG_FILE=str(config),
                       XDG_CACHE_HOME=str(work / '.cache'), TEXMFVAR=str(work / 'texmf-var'))
            source = work / 'example.md'
            source.write_text('''---
title: Schrijfworkflow
---

# Controle

Nederlandse tekst met café, ideeën en naïef. Referentie [@example].

::: {.epigraph oddpage=false pagestyle=plain pagebreak-before=false pagebreak-after=false}
> EPIGRAPHMARKER

Voorbeeldauteur
:::

::: nolatex
DOCXONLYMARKER
:::

\\newpage

AFTERBREAKMARKER
''')
            refs = work / 'example.json'
            refs.write_text(json.dumps([{'id': 'example', 'type': 'book', 'title': 'CitationTestTitle',
                                        'author': [{'family': 'Voorbeeld', 'given': 'Anna'}],
                                        'issued': {'date-parts': [[2024]]}}]))
            for kind, preset in [('docx', 'Custom - DOCX - standard.yaml'),
                                 ('pdf', 'Custom - PDF (LuaLaTeX) - standard.yaml')]:
                output = work / ('example.' + kind)
                result = subprocess.run(['pandoc', '--defaults', str(rendered(preset, work)),
                                         '--bibliography', str(refs), str(source), '-o', str(output)],
                                        env=env, capture_output=True, text=True, timeout=240)
                self.assertEqual(result.returncode, 0, result.stderr[-6000:])
                if kind == 'docx':
                    with zipfile.ZipFile(output) as archive:
                        body = archive.read('word/document.xml').decode()
                        text = body + archive.read('word/footnotes.xml').decode()
                    for marker in ['EPIGRAPHMARKER', 'DOCXONLYMARKER', 'AFTERBREAKMARKER', 'CitationTestTitle']:
                        self.assertIn(marker, text)
                    self.assertIn('w:type="page"', body)
                else:
                    text = subprocess.check_output(['pdftotext', str(output), '-'], text=True)
                    for marker in ['EPIGRAPHMARKER', 'AFTERBREAKMARKER', 'CitationTestTitle']:
                        self.assertIn(marker, text)
                    self.assertNotIn('DOCXONLYMARKER', text)
                    fonts = subprocess.check_output(['pdffonts', str(output)], text=True)
                    self.assertIn('EBGaramond', fonts)


if __name__ == '__main__':
    unittest.main()
