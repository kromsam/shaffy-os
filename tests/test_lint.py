"""`./bin/shaffy lint`: which files each tool sees, and what it reports (lib/shaffy/lint.py)."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))

from shaffy import lint


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
            'bin/old': '#!/usr/bin/env python3\nprint(1)\n',
            'bin/glue': '#!/usr/bin/env bash\ntrue\n',
            'scripts/run.sh': 'echo hi\n',
            'home/launcher': '#!/bin/sh\nexec true\n',
            'home/config.toml.tmpl': '#!/usr/bin/python3\n{{ .x }}\n',
            'home/.chezmoitemplates/skill.md': '{{- /* a template */ -}}\n# Skill\n',
            'roles/x/tasks/main.yml': '- name: X\n',
            'site.yml': '- hosts: localhost\n',
            '.github/workflows/ci.yml': 'on: push\n',
            'README.md': '# Readme\n',
            'vendored.lua': 'x = 1\n',
            'home/modify_settings.json': '#!/bin/sh\ncat\n',
            'home/dot_config/niri/config.kdl': 'input {\n}\n',
            'home/app.desktop': '[Desktop Entry]\n',
            '.yamllint': 'extends: default\n',
            'defaults/config.yaml': 'a: 1\n',
            'supplement.example/.github/workflows/shaffy.yml': 'on: push\n',
            'REUSE.toml': 'version = 1\n[[annotations]]\npath = "**"\n[[annotations]]\npath = ["vendored.lua"]\n',
        }
        for path, text in files.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text)
        (self.root / 'watermark.png').write_bytes(b'\x89PNG\r\n\x1a\n\0\0')
        self.calls = []

    def lint(self, paths, fix=False, results=None, missing=()):
        lines = []

        def call(command, **_):
            self.calls.append(command)
            return (results or {}).get(command[0], Finished())

        status = lint.run(
            paths,
            fix=fix,
            root=self.root,
            out=lines.append,
            call=call,
            is_available=lambda step, root: step.tool not in missing,
        )
        return status, lines

    def test_each_file_goes_to_its_language_and_the_rest_to_text(self):
        kinds = {
            path: lint.language(path, self.root)
            for path in (
                'lib/a.py',
                'bin/tool',
                'bin/old',
                'bin/glue',
                'scripts/run.sh',
                'home/launcher',
                'home/config.toml.tmpl',
                'home/.chezmoitemplates/skill.md',
                'roles/x/tasks/main.yml',
                'site.yml',
                '.github/workflows/ci.yml',
                'README.md',
                'REUSE.toml',
                'home/modify_settings.json',
                'home/dot_config/niri/config.kdl',
                'home/app.desktop',
                '.yamllint',
                'defaults/config.yaml',
                'supplement.example/.github/workflows/shaffy.yml',
                'watermark.png',
            )
        }
        self.assertEqual(
            kinds,
            {
                'lib/a.py': 'python',
                'bin/tool': 'python',
                'bin/old': 'python',
                'bin/glue': 'shell',
                'scripts/run.sh': 'shell',
                'home/launcher': 'shell',
                'home/config.toml.tmpl': 'text',
                'home/.chezmoitemplates/skill.md': 'text',
                'roles/x/tasks/main.yml': 'ansible',
                'site.yml': 'ansible',
                '.github/workflows/ci.yml': 'workflow',
                'README.md': 'markdown',
                'REUSE.toml': 'toml',
                'home/modify_settings.json': 'shell',
                'home/dot_config/niri/config.kdl': 'text',
                'home/app.desktop': 'text',
                '.yamllint': 'yaml',
                'defaults/config.yaml': 'yaml',
                'supplement.example/.github/workflows/shaffy.yml': 'workflow',
                'watermark.png': None,
            },
        )

    def test_every_fix_runs_before_any_check_and_a_project_tool_gets_no_files(self):
        status, lines = self.lint(['lib/a.py', 'bin/tool', 'site.yml', 'vendored.lua'], fix=True)
        self.assertEqual(status, 0)
        config = ['--config', str(ROOT / 'ruff.toml')]
        self.assertEqual(
            self.calls,
            [
                ['ruff', 'check', '--fix', *config, 'lib/a.py', 'bin/tool'],
                ['ruff', 'format', *config, 'lib/a.py', 'bin/tool'],
                ['ruff', 'check', *config, 'lib/a.py', 'bin/tool'],
                ['ruff', 'format', '--check', *config, 'lib/a.py', 'bin/tool'],
                ['ansible-lint'],
            ],
        )
        self.assertEqual(
            lines,
            [
                'interpreter: 2 file(s) clean',
                'ruff check: 2 file(s) clean',
                'ruff format: 2 file(s) clean',
                'ansible-lint: the playbooks clean',
            ],
        )

    def test_an_env_shebang_is_reported_and_fix_names_the_system_interpreter(self):
        status, lines = self.lint(['bin/old', 'bin/glue'])
        self.assertEqual(status, 1)
        self.assertEqual(
            lines[:3],
            [
                'interpreter: 2 file(s), problems remain',
                '  bin/old:1: #!/usr/bin/env python3 should be #!/usr/bin/python3',
                '  bin/glue:1: #!/usr/bin/env bash should be #!/bin/bash',
            ],
        )
        self.assertEqual(self.lint(['bin/old', 'bin/glue'], fix=True)[0], 0)
        self.assertEqual((self.root / 'bin/old').read_text(), '#!/usr/bin/python3\nprint(1)\n')
        self.assertEqual((self.root / 'bin/glue').read_text(), '#!/bin/bash\ntrue\n')

    def test_every_tool_gets_this_checkouts_configuration(self):
        # shfmt's flags are .editorconfig's [*] section: two spaces, indented case branches,
        # operators first on a continued line.
        self.assertEqual(lint.options('shfmt'), ['--indent', '2', '--case-indent', '--binary-next-line'])
        self.assertEqual(lint.options('yamllint'), ['--config-file', str(ROOT / '.yamllint')])
        self.assertEqual(lint.options('shellcheck'), ['--rcfile', str(ROOT / '.shellcheckrc')])
        self.assertEqual(lint.options('taplo'), ['--config', str(ROOT / '.taplo.toml')])
        self.assertEqual(lint.options('stylua'), ['--config-path', str(ROOT / 'stylua.toml')])
        self.assertEqual(lint.options('markdownlint-cli2'), ['--config', str(ROOT / '.markdownlint-cli2.yaml')])

    def test_problems_are_bounded_and_fail_the_run(self):
        output = '\n'.join(f'run.sh:{n}: SC2086' for n in range(1, 101))
        status, lines = self.lint(['scripts/run.sh'], results={'shellcheck': Finished(1, output)})
        self.assertEqual(status, 1)
        self.assertEqual(
            lines[:3],
            ['interpreter: 1 file(s) clean', 'shfmt: 1 file(s) clean', 'shellcheck: 1 file(s), problems remain'],
        )
        self.assertEqual(len(lines), 3 + lint.LINES + 1)
        self.assertIn('60 more lines', lines[-1])

    def test_a_missing_tool_fails_and_says_how_to_install_it(self):
        status, lines = self.lint(['README.md'], missing=('markdownlint-cli2',))
        self.assertEqual(status, 1)
        self.assertEqual(lines, [f'markdownlint-cli2: not installed; install it with {lint.INSTALL}'])
        self.assertEqual(self.calls, [])

    def test_nothing_lintable_is_clean(self):
        self.assertEqual(self.lint(['vendored.lua', 'watermark.png']), (0, ['Nothing to lint.']))

    def test_whitespace_follows_editorconfig_and_fix_mends_what_it_safely_can(self):
        (self.root / 'home/app.desktop').write_bytes(b'[Desktop Entry]\r\nName=x \n\tExec=x')
        status, lines = self.lint(['home/app.desktop'])
        self.assertEqual(status, 1)
        self.assertEqual(
            lines,
            [
                'whitespace: 1 file(s), problems remain',
                '  home/app.desktop: CR line ends; .editorconfig asks for LF',
                '  home/app.desktop: no final newline',
                '  home/app.desktop:2: trailing whitespace',
                '  home/app.desktop:3: indented with a tab',
            ],
        )
        status, lines = self.lint(['home/app.desktop'], fix=True)
        self.assertEqual(lines[1:], ['  home/app.desktop:3: indented with a tab'])
        self.assertEqual((self.root / 'home/app.desktop').read_bytes(), b'[Desktop Entry]\nName=x\n\tExec=x\n')

    def test_json_must_parse_and_keep_to_editorconfig(self):
        (self.root / 'settings.json').write_text('{"a": 1,}\n')
        status, lines = self.lint(['settings.json'])
        self.assertEqual(status, 1)
        self.assertEqual(lines[0], 'whitespace: 1 file(s) clean')
        self.assertEqual(lines[1], 'json: 1 file(s), problems remain')
        self.assertTrue(lines[2].startswith('  settings.json: Illegal trailing comma'), lines[2])
        (self.root / 'settings.json').write_text('{\n  // JSONC\n  "url": "http://x/*y*/", /* z */ "b": 2\n}\n')
        self.assertEqual(self.lint(['settings.json'])[0], 0)

    def test_a_workflow_is_yaml_too_and_each_tool_runs_once_over_all_its_files(self):
        status, _ = self.lint(['defaults/config.yaml', '.github/workflows/ci.yml'])
        self.assertEqual(status, 0)
        yamllint = [call for call in self.calls if call[0] == 'yamllint']
        self.assertEqual(len(yamllint), 1)
        self.assertEqual(yamllint[0][-2:], ['defaults/config.yaml', '.github/workflows/ci.yml'])
        self.assertEqual([call[0] for call in self.calls], ['yamllint', 'actionlint', 'zizmor'])

    def test_every_tool_is_a_declared_development_tool(self):
        declared = tomllib.loads((ROOT / 'mise.dev.toml').read_text())['tools']
        for step in lint.STEPS:
            if not step.system:
                package = step.package or step.tool
                self.assertTrue(any(name == package or name.endswith(':' + package) for name in declared), step.tool)


class SupplementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.supplement = Path(self.temporary.name) / 'supplement'
        self.supplement.mkdir()
        (self.supplement / 'config.yaml').write_text('schema_version: 1\nsupplement_id: t\n')
        self.runtime = Path(self.temporary.name) / 'runtime'

    def hook(self, cwd):
        event = {'hook_event_name': 'SessionStart', 'session_id': 's', 'cwd': str(cwd)}
        env = {'HOME': self.temporary.name, 'PATH': '/usr/bin:/bin', 'XDG_RUNTIME_DIR': str(self.runtime)}
        return subprocess.run(
            [str(ROOT / 'bin/shaffy'), 'lint', '--hook', '--supplement', str(self.supplement)],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_the_home_hook_acts_only_inside_the_supplement(self):
        outside = self.hook(ROOT)
        self.assertEqual((outside.returncode, outside.stdout, outside.stderr), (0, '', ''))
        self.assertFalse(self.runtime.exists())
        inside = self.hook(self.supplement)
        self.assertEqual(inside.returncode, 0, inside.stderr)
        self.assertEqual(len(list((self.runtime / 'shaffy/lint-sessions').glob('*.json'))), 1)


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'checkout'
        self.root.mkdir()
        self.state = Path(self.temporary.name) / 'session.json'
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / '.gitignore').write_text('supplement/\n')
        (self.root / 'supplement').mkdir()
        (self.root / 'supplement/x.py').write_text('X = 1\n')
        (self.root / 'owners.py').write_text('UNFINISHED = 1\n')  # dirty before the session starts
        (self.root / 'photo.png').write_bytes(b'\x89PNG\0')
        self.seen = []

    def fake(self, status, rewrite=None):
        def run(paths, fix=False, root=None, out=print, require=True):
            self.seen.append((paths, fix))
            if rewrite:
                (self.root / paths[0]).write_text(rewrite)
            out('ruff check: 1 file(s), problems remain' if status else 'ruff check: 1 file(s) clean')
            return status

        return run

    def hook(self, event, status=1, rewrite=None):
        return lint.hook(event, root=self.root, lint=self.fake(status, rewrite), state_file=self.state)

    def start(self, source='startup'):
        self.assertEqual(self.hook({'hook_event_name': 'SessionStart', 'source': source}), {})

    def edit(self, path):
        return {'hook_event_name': 'PostToolUse', 'tool_input': {'file_path': str(path)}}

    def test_an_edited_file_is_fixed_and_what_remains_goes_back_to_the_agent(self):
        self.start()
        (self.root / 'a.py').write_text('A = 1\n')
        reply = self.hook(self.edit(self.root / 'a.py'))
        self.assertEqual(self.seen, [(['a.py'], True)])
        context = reply['hookSpecificOutput']['additionalContext']
        self.assertTrue(context.startswith('ruff check: 1 file(s), problems remain\n'), context)
        self.assertEqual(self.hook(self.edit(self.root / 'a.py'), status=0), {})

    def test_the_agent_hears_when_its_file_was_reformatted(self):
        self.start()
        (self.root / 'a.py').write_text('A = 1\n')
        reply = self.hook(self.edit(self.root / 'a.py'), status=0, rewrite='A = 2\n')
        self.assertEqual(
            reply['hookSpecificOutput']['additionalContext'], 'Formatted a.py; read it again before the next edit.'
        )

    def test_an_edit_outside_the_checkout_ignored_or_of_another_kind_is_left_alone(self):
        self.start()
        for path in ('/etc/hosts', self.root / 'photo.png', self.root / 'supplement/x.py'):
            self.assertEqual(self.hook(self.edit(path)), {})
        self.assertEqual(self.seen, [])

    def test_a_patch_without_a_file_fixes_only_what_the_session_changed(self):
        self.start()
        (self.root / 'a.py').write_text('A = 1\n')
        reply = self.hook({'hook_event_name': 'PostToolUse', 'tool_input': {'command': '*** Begin Patch'}})
        self.assertEqual(self.seen, [(['a.py'], True)])
        self.assertIn('problems remain', reply['hookSpecificOutput']['additionalContext'])

    def test_a_stop_checks_the_sessions_files_once_and_not_the_owners(self):
        self.start()
        (self.root / 'a.py').write_text('A = 1\n')
        reply = self.hook({'hook_event_name': 'Stop'})
        self.assertEqual(reply['decision'], 'block')
        self.assertEqual(self.seen, [(['a.py'], False)])
        self.assertEqual(self.hook({'hook_event_name': 'Stop', 'stop_hook_active': True}), {})

    def test_a_clean_file_is_not_checked_again_until_it_changes(self):
        self.start()
        (self.root / 'a.py').write_text('A = 1\n')
        self.assertEqual(self.hook({'hook_event_name': 'Stop'}, status=0), {})
        self.assertEqual(self.hook({'hook_event_name': 'Stop'}), {})
        (self.root / 'a.py').write_text('A = 2\n')
        self.assertEqual(self.hook({'hook_event_name': 'Stop'})['decision'], 'block')
        self.assertEqual(self.seen, [(['a.py'], False), (['a.py'], False)])

    def test_an_owners_file_the_session_edits_becomes_the_sessions(self):
        self.start()
        (self.root / 'owners.py').write_text('UNFINISHED = 2\n')
        self.hook({'hook_event_name': 'Stop'})
        self.assertEqual(self.seen, [(['owners.py'], False)])

    def test_a_resumed_session_keeps_its_start(self):
        self.start()
        (self.root / 'a.py').write_text('A = 1\n')
        self.start(source='resume')
        self.hook({'hook_event_name': 'Stop'})
        self.assertEqual(self.seen, [(['a.py'], False)])

    def test_without_a_start_every_changed_file_is_checked_and_none_written(self):
        reply = self.hook({'hook_event_name': 'PostToolUse', 'tool_input': {}})
        self.assertEqual(self.seen, [(['.gitignore', 'owners.py'], False)])
        self.assertIn('problems remain', reply['hookSpecificOutput']['additionalContext'])

    def test_a_missing_tool_does_not_hold_the_agent_back(self):
        status = lint.run(
            ['owners.py'], root=self.root, out=lambda line: None, is_available=lambda step, root: False, require=False
        )
        self.assertEqual(status, 0)


if __name__ == '__main__':
    unittest.main()
