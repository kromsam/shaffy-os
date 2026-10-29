"""Converting a Logseq graph into a SilverBullet space, with a synthetic graph."""

from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/convert-logseq-graph.py'
CONVERT = runpy.run_path(str(SCRIPT))

GRAPH = {
    'logseq/config.edn': '{:file/name-format :triple-lowbar\n :journal/page-title-format "EEEE, dd-MM-yyyy"}\n',
    'journals/2024_01_15.md': """\
        - TODO call [[Example Person]] about #[[Example Project/Plans]]
        	- DONE draft #notes
        	  collapsed:: true
        	  :LOGBOOK:
        	  CLOCK: [2024-01-15 Mon 10:00:00]--[2024-01-15 Mon 10:05:00] =>  00:05:00
        	  :END:
        - NOW reading
          SCHEDULED: <2024-01-16 Tue>
        - ![photo](../assets/photo.png){:height 300, :width 400}
        - {{video https://video.example.invalid/watch?v=1}}
        - {{video https://www.youtube.com/watch?v=abcdefghijk&t=5}}
        - #Outing
          what:: A concert
          tag:: #music [[Example Person]]
          name:: Main stage
        - see [[Tuesday, 16-01-2024]]
        """,
    'journals/2024_01_16.md': '-\n',
    'pages/Example Project___Plans.md': """\
        tags:: [[planning]], [[example]]
        alias:: Plans
        source:: [[Monday, 15-01-2024]]

        - first idea
          original-title:: An Idea
          id:: 01234567-89ab-cdef-0123-456789abcdef
        - {{query (and [[planning]] [[example]])}}
        - ![diagram](../assets/diagram.png)
        - `[[not a link]]` stays code
        """,
    'pages/@ref2024%3A A Study.md': 'title:: @ref2024: A Study\n\n- {{zotero-imported-file ABCD1234, "study.pdf"}}\n',
    'pages/Outings.md': """\
        - {{query (and [[Outing]] (not [[Example Person]]))}}
          query-table:: true
          query-properties:: [:what :tag]
          query-sort-by:: what
          query-sort-desc:: true
        - {{query [[TODO]]}}
        - {{query "full text"}}
        """,
    'pages/Templates.md': """\
        - Outing
          template:: Outing
          template-including-parent:: false
        	- #Outing
        	  what:: X
        """,
    'pages/Reading List.md': '- #books #[[to read]]\n- # Reading List\n- see #@ref2024 and #2024, then #books.\n',
    'pages/Example Person.md': '- met in [[Plans]]\n- line in both copies\n',
    'pages/Example Person.sync-conflict-20240101-120000-ABCDEFG.md': '- met in [[Plans]]\n- a line only the copy has\n',
    'pages/Same.md': '- kept\n',
    'pages/Same.sync-conflict-20240101-120000-ABCDEFG.md': '- kept\n',
    'whiteboards/board.edn': '{}\n',
    'assets/photo.png': 'png',
    'assets/diagram.png': 'png',
}


class LogseqConversionTests(unittest.TestCase):
    def convert(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        graph, space = Path(work.name) / 'graph', Path(work.name) / 'space'
        for name, text in GRAPH.items():
            (graph / name).parent.mkdir(parents=True, exist_ok=True)
            (graph / name).write_text(textwrap.dedent(text))
        before = {p: p.read_bytes() for p in graph.rglob('*') if p.is_file()}
        result = subprocess.run(
            [sys.executable, '-I', str(SCRIPT), str(graph), str(space)], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p: p.read_bytes() for p in graph.rglob('*') if p.is_file()}, before, 'the graph is only read')
        return space

    def test_journals_tasks_and_links_follow_silverbullet_conventions(self):
        space = self.convert()
        journal = (space / 'Journal/2024-01-15.md').read_text()
        self.assertTrue(
            journal.startswith('---\ntags: [journal]\ndate: 2024-01-15\n---\n'), 'as the journal template writes'
        )
        self.assertIn('- [ ] call [[Example Person]] about [[Example Project/Plans]]', journal)
        self.assertIn('  - [x] draft #notes', journal)
        self.assertIn('- [DOING] reading\n  [scheduled: 2024-01-16]', journal)
        self.assertIn('- ![[assets/photo.png|400x300]]', journal)
        self.assertIn('- https://video.example.invalid/watch?v=1', journal)
        self.assertIn('- ${embed.youtube "https://youtu.be/abcdefghijk"}', journal)
        # Block tags become hashtags, and a property named like a built-in attribute is renamed.
        self.assertIn('- #Outing\n  [what: A concert]\n  #music #<Example Person>\n  [logseqName: Main stage]', journal)
        self.assertIn('[[Journal/2024-01-16]]', journal)
        for logseq in ('collapsed::', 'LOGBOOK', 'CLOCK', 'TODO', 'DONE', '\t'):
            self.assertNotIn(logseq, journal)
        self.assertFalse((space / 'Journal/2024-01-16.md').exists(), 'empty journals are left out')

    def test_page_properties_become_frontmatter_and_block_properties_attributes(self):
        space = self.convert()
        text = (space / 'Example Project/Plans.md').read_text()
        front, body = text.split('---\n')[1:3]
        self.assertEqual(
            yaml.safe_load(front),
            {'tags': ['planning', 'example'], 'aliases': ['Plans'], 'source': '[[Journal/2024-01-15]]'},
        )
        self.assertIn('- first idea\n  [originalTitle: An Idea]\n', body)
        self.assertNotIn('id::', body)
        self.assertIn(
            '- ${query[[from o = logseq.blocks() where (logseq.refers(o, "planning") and '
            'logseq.refers(o, "example")) select logseq.render(o)]]}',
            body,
        )
        self.assertIn('![[assets/diagram.png]]', body)
        self.assertIn('`[[not a link]]` stays code', body)
        self.assertTrue((space / 'assets/photo.png').exists())

    def test_page_tags_move_to_frontmatter_and_the_title_is_not_restated(self):
        space = self.convert()
        front, body = (space / 'Reading List.md').read_text().split('---\n')[1:3]
        self.assertEqual(yaml.safe_load(front), {'tags': ['books', 'to read']})
        self.assertNotIn('# Reading List', body)
        # A hashtag SilverBullet cannot parse becomes a link to the page it named.
        self.assertEqual(body, '- see [[ref2024]] and [[2024]], then #books.\n')

    def test_simple_queries_become_silverbullet_queries(self):
        space = self.convert()
        outings = (space / 'Outings.md').read_text().split('\n')
        self.assertEqual(
            outings[0],
            '- ${query[[from o = logseq.blocks() where (logseq.refers(o, "Outing") and '
            'not (logseq.refers(o, "Example Person"))) order by o.what desc nulls last '
            'select {page = "[[" .. o.page .. "]]", what = o.what, '
            'tags = table.concat(o.tags or {}, " ")}]]}',
        )
        self.assertEqual(
            outings[1], '- ${query[[from o = logseq.blocks() where logseq.marker(o, "TODO") select logseq.render(o)]]}'
        )
        self.assertEqual(outings[2], '- `{{query "full text"}}`', 'what it cannot translate stays as code')
        library = (space / 'Library/Logseq.md').read_text()
        self.assertTrue(library.startswith('---\ntags: meta\n---\n'))
        for helper in (
            'function logseq.refers(o, name)',
            'function logseq.marker(o, marker)',
            'function logseq.blocks()',
            'function logseq.render(o)',
        ):
            self.assertIn(helper, library)
        report = (space / 'Logseq import.md').read_text()
        self.assertTrue(report.startswith('---\ntags: meta/logseq-import\n---\n'), 'kept out of queries')
        self.assertIn('* [[Outings]]', report.split('# Queries to rewrite')[1].split('#')[0])
        self.assertIn('* [[Outings]]', report.split('# Converted queries')[1].split('#')[0])
        self.assertNotIn('* [ ]', report, 'a checkbox would show as a linked task on the page')

    def test_templates_become_slash_templates(self):
        space = self.convert()
        self.assertEqual(
            (space / 'Library/Templates/Outing.md').read_text(),
            '---\ntags: meta/template/slash\ndescription: Logseq template Outing\n---\n- #Outing\n  [what: X]\n',
        )
        self.assertEqual(
            (space / 'Templates.md').read_text(), '- Slash template /Outing: [[Library/Templates/Outing]]\n'
        )

    def test_invalid_names_are_cleaned_and_reported(self):
        space = self.convert()
        page = (space / 'ref2024: A Study.md').read_text()
        self.assertEqual(yaml.safe_load(page.split('---\n')[1]), {'displayName': '@ref2024: A Study'})
        self.assertIn('[study.pdf](zotero://open-pdf/library/items/ABCD1234)', page)
        report = (space / 'Logseq import.md').read_text()
        self.assertIn('`@ref2024: A Study` → [[ref2024: A Study]]', report)
        self.assertIn('* [[Example Project/Plans]]', report.split('# Converted queries')[1])
        self.assertIn('`board.edn`', report)

    def test_conflict_copies_contribute_only_their_new_lines(self):
        space = self.convert()
        self.assertFalse(list(space.rglob('*sync-conflict*')))
        report = (space / 'Logseq import.md').read_text()
        conflicts = report.split('# Syncthing conflict copies')[1]
        self.assertIn('## [[Example Person]]\n```\n- a line only the copy has\n```', conflicts)
        self.assertNotIn('[[Same]]', conflicts)
        self.assertNotIn('line in both copies', conflicts)

    def test_an_existing_target_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            graph = Path(directory) / 'graph'
            (graph / 'pages').mkdir(parents=True)
            result = subprocess.run(
                [sys.executable, '-I', str(SCRIPT), str(graph), directory], capture_output=True, text=True, check=False
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('exists', result.stderr)

    def test_names_satisfy_silverbullet_rules(self):
        clean = CONVERT['clean_name']
        self.assertEqual(clean('@key|part#2'), 'key-part-2')
        self.assertEqual(clean('.hidden/../page.md'), 'hidden/page')
        self.assertEqual(
            CONVERT['journal_date']('Jan 5th, 2024', CONVERT['title_pattern']('MMM do, yyyy')).isoformat(), '2024-01-05'
        )


if __name__ == '__main__':
    unittest.main()
