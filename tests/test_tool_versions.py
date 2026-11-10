"""Installed tools meet the recorded minimum versions, ruff targets the system Python and CI
uses the recorded container."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy import pins

RECORD = json.loads((ROOT / 'tests/tool-versions.json').read_text())
# chezmoi's own record of its minimum, which it enforces before reading the home.
MINIMUM = RECORD['minimum'] | {'chezmoi': (ROOT / 'home/.chezmoiversion').read_text().strip()}
COMMANDS = {
    'ansible-core': ['ansible', '--version'],
    'chezmoi': ['chezmoi', '--version'],
    'git': ['git', '--version'],
    'niri': ['niri', '--version'],
    'noctalia': ['noctalia', '--version'],
    'fish': ['fish', '--version'],
}


def version_tuple(text):
    return tuple(int(part) for part in re.findall(r'\d+', text)[:3])


class ToolVersionTests(unittest.TestCase):
    def test_python_is_new_enough(self):
        self.assertGreaterEqual(sys.version_info[:2], version_tuple(MINIMUM['python'])[:2])

    def test_installed_tools_are_new_enough(self):
        for tool, command in COMMANDS.items():
            with self.subTest(tool=tool):
                if shutil.which(command[0]) is None:
                    self.skipTest(f'{tool} is not installed here')
                output = subprocess.run(command, capture_output=True, text=True, check=False).stdout
                found = re.search(r'(\d+\.\d+(?:\.\d+)?)', output)
                self.assertIsNotNone(found, f'no version in {output!r}')
                self.assertGreaterEqual(version_tuple(found.group(1)), version_tuple(MINIMUM[tool]))

    def test_ruff_targets_the_system_python(self):
        # CI upgrades its packages first, so this fails as soon as CachyOS ships a newer Python;
        # scripts/refresh-pins.py raises the target.
        system = subprocess.run(
            ['/usr/bin/python3', '-c', 'import sys; print(*sys.version_info[:2], sep="")'],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        self.assertIn(f'\ntarget-version = "py{system}"\n', (ROOT / 'ruff.toml').read_text())

    def test_the_supplement_workflow_sees_the_shaffy_os_releases(self):
        # A supplement's minimum_release is checked against the release tags HEAD contains;
        # a shallow checkout has none, and every supplement that sets one fails.
        steps = yaml.safe_load((ROOT / '.github/workflows/supplement.yml').read_text())['jobs']['supplement']['steps']
        checkout = next(step for step in steps if step.get('with', {}).get('repository') == 'kromsam/shaffy-os')
        self.assertEqual(checkout['with']['fetch-depth'], 0)

    def test_ci_uses_the_recorded_container(self):
        for name in pins.WORKFLOWS:
            workflow = (ROOT / name).read_text()
            self.assertIn(f'image: {RECORD["ci_container"]}', workflow, name)


if __name__ == '__main__':
    unittest.main()
