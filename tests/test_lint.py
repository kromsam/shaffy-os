"""`./bin/shaffy lint`: which files each tool sees, and what it reports (lib/shaffy/lint.py)."""
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))

from shaffy import lint  # noqa: E402


class Finished:
    def __init__(self, returncode=0, stdout=''):
        self.returncode, self.stdout, self.stderr = returncode, stdout, ''


class LintTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        files = {
            'lib/a.py': 'A = 1\n',
            'bin/tool': '#!/usr/bin/python3\nprint(1)\n',
            'scripts/run.sh': 'echo hi\n',
            'home/launcher': '#!/bin/sh\nexec true\n',
            'home/config.toml.tmpl': '#!/usr/bin/python3\n{{ .x }}\n',
            'roles/x/tasks/main.yml': '- name: X\n',
            'site.yml': '- hosts: localhost\n',
            '.github/workflows/ci.yml': 'on: push\n',
            'README.md': '# Readme\n',
            'vendored.lua': 'x = 1\n',
            'REUSE.toml': 'version = 1\n[[annotations]]\npath = "**"\n[[annotations]]\npath = ["vendored.lua"]\n',
        }
        for path, text in files.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text)
        self.calls = []

    def lint(self, paths, fix=False, results=None, missing=()):
        lines = []

        def call(command, **_):
            self.calls.append(command)
            return (results or {}).get(command[3] if command[0] == 'mise' else command[0], Finished())

        status = lint.run(paths, fix=fix, root=self.root, out=lines.append, call=call,
                          is_available=lambda step, root: step.tool not in missing)
        return status, lines

    def test_each_file_goes_to_its_language_and_templates_to_none(self):
        kinds = {path: lint.language(path, self.root) for path in
                 ('lib/a.py', 'bin/tool', 'scripts/run.sh', 'home/launcher', 'home/config.toml.tmpl',
                  'roles/x/tasks/main.yml', 'site.yml', '.github/workflows/ci.yml', 'README.md', 'REUSE.toml')}
        self.assertEqual(kinds, {'lib/a.py': 'python', 'bin/tool': 'python', 'scripts/run.sh': 'shell',
                                 'home/launcher': 'shell', 'home/config.toml.tmpl': None,
                                 'roles/x/tasks/main.yml': 'ansible', 'site.yml': 'ansible',
                                 '.github/workflows/ci.yml': 'workflow', 'README.md': 'markdown',
                                 'REUSE.toml': None})

    def test_fix_rewrites_before_it_checks_and_a_project_tool_gets_no_files(self):
        status, lines = self.lint(['lib/a.py', 'bin/tool', 'site.yml', 'vendored.lua'], fix=True)
        self.assertEqual(status, 0)
        self.assertEqual(self.calls, [
            ['mise', 'exec', '--', 'ruff', 'check', '--fix', 'lib/a.py', 'bin/tool'],
            ['mise', 'exec', '--', 'ruff', 'check', 'lib/a.py', 'bin/tool'],
            ['mise', 'exec', '--', 'ruff', 'format', 'lib/a.py', 'bin/tool'],
            ['mise', 'exec', '--', 'ruff', 'format', '--check', 'lib/a.py', 'bin/tool'],
            ['mise', 'exec', '--', 'ansible-lint'],
        ])
        self.assertEqual(lines, ['ruff check: 2 file(s) clean', 'ruff format: 2 file(s) clean',
                                 'ansible-lint: the playbooks clean'])

    def test_problems_are_bounded_and_fail_the_run(self):
        output = '\n'.join(f'run.sh:{n}: SC2086' for n in range(1, 101))
        status, lines = self.lint(['scripts/run.sh'], results={'shellcheck': Finished(1, output)})
        self.assertEqual(status, 1)
        self.assertEqual(lines[0], 'shfmt: 1 file(s) clean')
        self.assertEqual(lines[1], 'shellcheck: 1 file(s), problems remain')
        self.assertEqual(len(lines), 2 + lint.LINES + 1)
        self.assertIn('60 more lines', lines[-1])

    def test_a_missing_tool_fails_and_says_how_to_install_it(self):
        status, lines = self.lint(['README.md'], missing=('markdownlint-cli2',))
        self.assertEqual(status, 1)
        self.assertEqual(lines, [f'markdownlint-cli2: not installed; install it with {lint.INSTALL}'])
        self.assertEqual(self.calls, [])

    def test_nothing_lintable_is_clean(self):
        self.assertEqual(self.lint(['vendored.lua', 'home/config.toml.tmpl']), (0, ['Nothing to lint.']))

    def test_every_tool_is_a_declared_development_tool(self):
        declared = tomllib.loads((ROOT / 'mise.dev.toml').read_text())['tools']
        for step in lint.STEPS:
            if not step.system:
                self.assertTrue(any(name == step.tool or name.endswith(':' + step.tool) for name in declared),
                                step.tool)


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / 'a.py').write_text('A = 1\n')
        (self.root / 'notes.txt').write_text('x\n')
        self.seen = []

    def fake(self, status, rewrite=None):
        def run(paths, fix=False, root=None, out=print, require=True):
            self.seen.append((paths, fix, require))
            if rewrite:
                (self.root / paths[0]).write_text(rewrite)
            out('ruff check: 1 file(s), problems remain' if status else 'ruff check: 1 file(s) clean')
            return status
        return run

    def edit(self, path):
        return {'hook_event_name': 'PostToolUse', 'tool_input': {'file_path': str(path)}}

    def test_an_edited_file_is_fixed_and_what_remains_goes_back_to_the_agent(self):
        reply = lint.hook(self.edit(self.root / 'a.py'), root=self.root, lint=self.fake(1))
        self.assertEqual(self.seen, [(['a.py'], True, False)])
        context = reply['hookSpecificOutput']['additionalContext']
        self.assertTrue(context.startswith('ruff check: 1 file(s), problems remain\n'), context)
        self.assertEqual(lint.hook(self.edit(self.root / 'a.py'), root=self.root, lint=self.fake(0)), {})

    def test_the_agent_hears_when_its_file_was_reformatted(self):
        reply = lint.hook(self.edit(self.root / 'a.py'), root=self.root, lint=self.fake(0, rewrite='A = 2\n'))
        self.assertEqual(reply['hookSpecificOutput']['additionalContext'],
                         'Formatted a.py; read it again before the next edit.')

    def test_an_edit_outside_the_checkout_or_to_another_kind_of_file_is_left_alone(self):
        for path in ('/etc/hosts', self.root / 'notes.txt'):
            self.assertEqual(lint.hook(self.edit(path), root=self.root, lint=self.fake(1)), {})
        self.assertEqual(self.seen, [])

    def test_a_stop_checks_the_changed_files_once_without_writing(self):
        reply = lint.hook({'hook_event_name': 'Stop'}, root=self.root, lint=self.fake(1))
        self.assertEqual(reply['decision'], 'block')
        self.assertEqual(self.seen, [(['a.py'], False, False)])
        again = lint.hook({'hook_event_name': 'Stop', 'stop_hook_active': True}, root=self.root,
                          lint=self.fake(1))
        self.assertEqual(again, {})
        self.assertEqual(lint.hook({'hook_event_name': 'Stop'}, root=self.root, lint=self.fake(0)), {})

    def test_a_missing_tool_does_not_hold_the_agent_back(self):
        status = lint.run(['a.py'], root=self.root, out=lambda line: None, is_available=lambda step, root: False,
                          require=False)
        self.assertEqual(status, 0)


if __name__ == '__main__':
    unittest.main()
