"""Transitions with a removal date: retirements and one-off migrations.

A retirement (an entry of home/.chezmoiremove or defaults/packages/retired.yml, a
public MCP server set to `enabled: false`) or a one-off migration (its script, test
and runbook) matters only until every installation has applied it. Each carries a
comment with `Remove after` and a date, three months after the release that made it.
`./bin/shaffy outdated` names those past their date; they are deleted on dev together
with what the comment says they cover (CLAUDE.md, "Retirements and migrations").
"""
import datetime
from pathlib import Path
import re

from .source import _files

MARKER = re.compile(r'Remove after ([0-9]{4}-[0-9]{2}-[0-9]{2})')
# Tests hold synthetic markers; a migration's own test goes with its script.
SKIP = ('tests/',)


def markers(root):
    """Every marker in the checkout's files, as (path, line number, date, line)."""
    found = []
    for path in _files(root):
        target = Path(root) / path
        if path.startswith(SKIP) or target.is_symlink() or not target.is_file():
            continue
        try:
            text = target.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for match in MARKER.finditer(line):
                found.append((path, number, datetime.date.fromisoformat(match.group(1)), line.strip()))
    return found


def due(root, today=None):
    """The markers whose date has passed."""
    today = today or datetime.date.today()
    return [marker for marker in markers(root) if marker[2] < today]
