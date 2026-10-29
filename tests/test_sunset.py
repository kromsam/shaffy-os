"""Retirements and one-off migrations carry a removal date (lib/shaffy/sunset.py)."""

import datetime
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))

from shaffy import sunset
from shaffy.updates import removal_section


def blocks(lines):
    """Each entry line with whether the comment block above it names a removal date."""
    marked, previous_was_comment = False, False
    for line in lines:
        comment = line.lstrip().startswith('#')
        if comment and not previous_was_comment:
            marked = False
        if comment:
            marked = marked or bool(sunset.MARKER.search(line))
        elif line.strip():
            yield line.strip(), marked
        previous_was_comment = comment


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        (self.repo / 'tests').mkdir()
        (self.repo / 'old.py').write_text('"""A migration.\n\nRemove after 2027-01-16.\n"""\n')
        (self.repo / 'new.md').write_text('<!-- Remove after 2027-03-01, with old.py. -->\n')
        (self.repo / 'tests/test_old.py').write_text("MARKER = '# Remove after 2000-01-01'\n")

    def test_markers_are_found_outside_the_tests_and_due_after_their_date(self):
        self.assertEqual(
            [(path, number, str(date)) for path, number, date, _ in sunset.markers(self.repo)],
            [('new.md', 1, '2027-03-01'), ('old.py', 3, '2027-01-16')],
        )
        self.assertEqual(sunset.due(self.repo, datetime.date(2027, 1, 16)), [])
        self.assertEqual([path for path, *_ in sunset.due(self.repo, datetime.date(2027, 2, 1))], ['old.py'])

    def test_outdated_names_each_due_marker(self):
        section = removal_section(self.repo, datetime.date(2027, 2, 1))
        self.assertEqual(section.summary, '1 past their date; delete them on dev')
        self.assertEqual(section.lines, ['old.py:3: Remove after 2027-01-16.'])
        self.assertEqual(removal_section(self.repo, datetime.date(2027, 1, 1)).summary, 'none due')


class RetirementsCarryADateTests(unittest.TestCase):
    def assert_marked(self, path, lines):
        for entry, marked in blocks(lines):
            self.assertTrue(marked, f'{path}: "{entry}" needs "Remove after YYYY-MM-DD" in the comment above it')

    def test_every_chezmoi_removal(self):
        path = 'home/.chezmoiremove'
        self.assert_marked(path, (ROOT / path).read_text().splitlines())

    def test_every_retired_package(self):
        path = 'defaults/packages/retired.yml'
        lines = (ROOT / path).read_text().splitlines()
        self.assert_marked(path, lines[lines.index('retired:') + 1 :])

    def test_every_public_mcp_server_retirement(self):
        path = 'defaults/config.yaml'
        text = (ROOT / path).read_text()
        servers = yaml.safe_load(text)['ai']['mcp_servers']
        lines = text.splitlines()
        for name in [name for name, server in servers.items() if server.get('enabled') is False]:
            above = lines[: lines.index(f'    {name}:')]
            comments = []
            while above and above[-1].lstrip().startswith('#'):
                comments.append(above.pop())
            self.assertTrue(any(sunset.MARKER.search(line) for line in comments), f'{path}: {name}')

    def test_every_migration_runbook(self):
        for runbook in sorted((ROOT / 'docs').glob('MIGRATING-*.md')):
            self.assertRegex(runbook.read_text(), sunset.MARKER, runbook.name)


if __name__ == '__main__':
    unittest.main()
