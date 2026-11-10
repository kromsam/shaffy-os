"""The bound supplement's private values stay out of the public tree.

A local test: it reads the supplement's denylist.txt, one Python regular expression
per line applied case-insensitively, and the identifying values of its settings, and
scans every public path and line. It reports where a value matched, never the value.
Without a supplement it is skipped.
"""

import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy import literals


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
        from shaffy.loader import parse

        try:
            return literals.supplement_rules(self.supplement, parse(self.supplement / 'config.yaml'))
        except ValueError as error:
            self.fail(str(error).replace(str(self.supplement) + '/', ''))

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
