"""The bound supplement's private values stay out of the public tree.

A local test: it reads the supplement's denylist.txt, one Python regular expression
per line applied case-insensitively, and the identifying values of its settings, and
scans every public path and line. It reports where a value matched, never the value.
Without a supplement it is skipped.
"""

import os
from pathlib import Path
import re
import sys
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy import literals

# Settings whose values identify the owner; paths and generic values match public conventions.
FIELDS = (
    'supplement_id',
    'identity.name',
    'identity.email',
    'backup.host',
    'backup.repository.id',
    'backup.repository.sftp.host',
    'backup.repository.sftp.user',
    'backup.repository.sftp.host_key',
    'tailnet.login_server',
    'tailnet.address',
)


def field(settings, dotted):
    for key in dotted.split('.'):
        settings = settings.get(key) if isinstance(settings, dict) else None
    if not isinstance(settings, str):
        return None
    if dotted.endswith('login_server'):
        return urlsplit(settings).hostname
    if dotted.endswith('host_key'):
        return max(settings.split(), key=len)  # the key body, not its type
    return settings


class ExportDenylistTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get('SHAFFY_SKIP_HOST_TESTS') == '1':
            self.skipTest('needs the bound supplement of this machine')
        from shaffy.selection import resolve

        selection = resolve()  # a broken binding fails here instead of skipping
        if selection['mode'] != 'supplement':
            self.skipTest('no supplement is bound')
        self.supplement = Path(selection['path'])
        if not (self.supplement / 'denylist.txt').is_file():
            self.skipTest(f'{self.supplement / "denylist.txt"} does not exist')

    def rules(self):
        rules = []
        for number, line in enumerate((self.supplement / 'denylist.txt').read_text().splitlines(), 1):
            if line.strip() and not line.lstrip().startswith('#'):
                try:
                    compiled = re.compile(line, re.IGNORECASE)
                except re.error:
                    self.fail(f'denylist.txt line {number} is not a valid regular expression')
                rules.append((f'denylist.txt line {number}', compiled))
        self.assertTrue(rules, 'denylist.txt holds no patterns')
        from shaffy.loader import load

        settings = load(ROOT, supplement=str(self.supplement))['shaffy']['settings']
        for name in FIELDS:
            value = field(settings, name)
            if value and len(value) >= 6:
                rules.append(
                    (
                        f'settings field {name}',
                        re.compile(r'(?<![A-Za-z0-9])' + re.escape(value) + r'(?![A-Za-z0-9])', re.IGNORECASE),
                    )
                )
        return [
            (rule, lambda line, previous='', c=compiled: [m.group(0) for m in c.finditer(line)])
            for rule, compiled in rules
        ]

    def test_public_files_hold_no_private_value(self):
        files = literals.public_files(ROOT)
        hits = literals.scan(ROOT, files, self.rules())
        where = sorted({f'{h.path}:{h.line}: {h.rule}' for h in hits})
        if where:
            self.fail(
                f'{len(where)} public line(s) or path(s) hold a private value (line 0 is the path):\n'
                + '\n'.join(where[:200])
            )


if __name__ == '__main__':
    unittest.main()
