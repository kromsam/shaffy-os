"""Installed tools meet the recorded minimum versions; CI uses the recorded container."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
RECORD = json.loads((ROOT / 'tests/tool-versions.json').read_text())
COMMANDS = {'ansible-core': ['ansible', '--version'], 'chezmoi': ['chezmoi', '--version'], 'git': ['git', '--version'],
            'niri': ['niri', '--version'], 'noctalia': ['noctalia', '--version'],
            'fish': ['fish', '--version']}


def version_tuple(text):
    return tuple(int(part) for part in re.findall(r'\d+', text)[:3])


class ToolVersionTests(unittest.TestCase):
    def test_python_is_new_enough(self):
        self.assertGreaterEqual(sys.version_info[:2], version_tuple(RECORD['minimum']['python'])[:2])

    def test_installed_tools_are_new_enough(self):
        for tool, command in COMMANDS.items():
            with self.subTest(tool=tool):
                if shutil.which(command[0]) is None:
                    self.skipTest(f'{tool} is not installed here')
                output = subprocess.run(command, capture_output=True, text=True, check=False).stdout
                found = re.search(r'(\d+\.\d+(?:\.\d+)?)', output)
                self.assertIsNotNone(found, f'no version in {output!r}')
                self.assertGreaterEqual(version_tuple(found.group(1)),
                                        version_tuple(RECORD['minimum'][tool]))

    def test_ci_uses_the_recorded_container(self):
        workflow = (ROOT / '.github/workflows/selftest.yml').read_text()
        self.assertIn(f"image: {RECORD['ci_container']}", workflow)


if __name__ == '__main__':
    unittest.main()
