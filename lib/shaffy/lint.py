"""`./bin/shaffy lint`: one formatter and linter per language, over the checkout (STYLE.md).

The tools come from the checkout's development tools (mise.dev.toml, run through
`mise exec` with MISE_ENV=dev), except fish_indent, which comes with fish. Templates
(`*.tmpl`, and everything in `home/.chezmoitemplates/`) are left out: no formatter reads their template syntax, and the sandboxed
render checks what they produce. So are the files REUSE.toml attributes to another
project, which keep their upstream's form. Without --fix nothing is written; with it the
formatters and the linters' safe fixes rewrite the files first. Each tool's output is
cut to a bounded number of lines, and the exit status says whether anything remains.

`--hook` reads a Claude Code or Codex hook event on stdin and answers in their JSON.
After an edit it fixes and checks the edited file, and tells the agent when it
reformatted it; after any other tool, and when the agent stops, it checks the files
that differ from HEAD without writing. What remains goes back to the agent, and a
stop is held back once. A tool that is not installed is skipped there, not reported.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

from .source import _files

ROOT = Path(__file__).resolve().parents[2]
LINES = 40
INSTALL = 'mise trust mise.dev.toml and MISE_ENV=dev mise install, in the checkout'


@dataclass(frozen=True)
class Step:
    language: str
    tool: str
    check: tuple
    # Arguments that rewrite the files, run first with --fix; None: the tool only checks.
    fix: tuple = None
    # Runs on the whole checkout, configured by its own file, rather than on the files given.
    project: bool = False
    # Comes with the system rather than mise.dev.toml.
    system: bool = False


STEPS = (
    Step('python', 'ruff', ('check',), fix=('check', '--fix')),
    Step('python', 'ruff', ('format', '--check'), fix=('format',)),
    Step('shell', 'shfmt', ('--diff',), fix=('--write',)),
    Step('shell', 'shellcheck', ()),
    Step('fish', 'fish_indent', ('--check',), fix=('--write',), system=True),
    Step('lua', 'stylua', ('--check',), fix=()),
    Step('markdown', 'markdownlint-cli2', (), fix=('--fix',)),
    Step('ansible', 'ansible-lint', (), project=True),
    Step('workflow', 'actionlint', ()),
)


def language(path, root=ROOT):
    """The language a file is linted as, or None."""
    target = root / path
    if (
        path.endswith('.tmpl')
        or path.startswith('home/.chezmoitemplates/')
        or target.is_symlink()
        or not target.is_file()
    ):
        return None
    suffix = Path(path).suffix
    if path in ('site.yml', 'records.yml') or (path.startswith('roles/') and suffix == '.yml'):
        return 'ansible'
    if path.startswith('.github/workflows/') and suffix == '.yml':
        return 'workflow'
    named = {'.py': 'python', '.sh': 'shell', '.fish': 'fish', '.lua': 'lua', '.md': 'markdown'}
    if suffix in named:
        return named[suffix]
    with open(target, 'rb') as file:
        first = file.readline(64)
    if first.startswith(b'#!/usr/bin/python3'):
        return 'python'
    if first.startswith((b'#!/bin/bash', b'#!/bin/sh')):
        return 'shell'
    return None


def third_party(root=ROOT):
    """The files REUSE.toml attributes to another project."""
    reuse = root / 'REUSE.toml'
    paths = set()
    for annotation in tomllib.loads(reuse.read_text())['annotations'] if reuse.exists() else []:
        listed = annotation['path']
        if listed != '**':
            paths.update([listed] if isinstance(listed, str) else listed)
    return paths


def changed(root=ROOT):
    """Files that differ from HEAD, and untracked ones Git does not ignore."""

    def listing(*arguments):
        result = subprocess.run(['git', '-C', str(root), *arguments, '-z'], capture_output=True, text=True, check=False)
        return [path for path in result.stdout.split('\0') if path]

    return sorted(
        set(listing('diff', '--name-only', 'HEAD')) | set(listing('ls-files', '--others', '--exclude-standard'))
    )


def _environment():
    return dict(os.environ, MISE_ENV='dev')


def available(step, root=ROOT):
    if step.system:
        return shutil.which(step.tool) is not None
    if shutil.which('mise') is None:
        return False
    result = subprocess.run(
        ['mise', 'which', step.tool], cwd=root, env=_environment(), capture_output=True, check=False
    )
    return result.returncode == 0


def _command(step, arguments, files):
    command = [step.tool, *arguments, *([] if step.project else files)]
    return command if step.system else ['mise', 'exec', '--', *command]


def _bounded(text):
    lines = text.rstrip().splitlines()
    if len(lines) <= LINES:
        return lines
    return lines[:LINES] + [f'… {len(lines) - LINES} more lines; run the tool on these files to see them all']


def run(paths, fix=False, root=ROOT, out=print, call=subprocess.run, is_available=available, require=True):
    """Lint the paths; returns 0 when every tool is clean (and installed, if required)."""
    by_language, upstream = {}, third_party(root)
    for path in paths:
        kind = None if path in upstream else language(path, root)
        if kind:
            by_language.setdefault(kind, []).append(path)
    if not by_language:
        out('Nothing to lint.')
        return 0
    failed = False
    for step in STEPS:
        files = by_language.get(step.language)
        if not files:
            continue
        label = f'{step.tool} {step.check[0]}' if step.check and not step.check[0].startswith('-') else step.tool
        if not is_available(step, root):
            if require:
                out(f'{label}: not installed; install it with {INSTALL}')
                failed = True
            continue
        if fix and step.fix is not None:
            call(
                _command(step, step.fix, files),
                cwd=root,
                env=_environment(),
                capture_output=True,
                text=True,
                check=False,
            )
        result = call(
            _command(step, step.check, files), cwd=root, env=_environment(), capture_output=True, text=True, check=False
        )
        scope = 'the playbooks' if step.project else f'{len(files)} file(s)'
        if result.returncode == 0:
            out(f'{label}: {scope} clean')
            continue
        failed = True
        out(f'{label}: {scope}, problems remain')
        for line in _bounded(result.stdout + result.stderr):
            out(f'  {line}')
    return 1 if failed else 0


def hook(event, root=ROOT, lint=run):
    """Claude Code or Codex hook: the JSON reply, or {} when there is nothing to say."""
    name = event.get('hook_event_name')
    if name == 'Stop' and event.get('stop_hook_active'):
        return {}
    edited = (event.get('tool_input') or {}).get('file_path')
    report, notes = [], []
    if name == 'PostToolUse' and edited:
        path = os.path.relpath(Path(edited).resolve(), root)
        if path.startswith('..') or not language(path, root):
            return {}
        before = (root / path).read_bytes()
        status = lint([path], fix=True, root=root, out=report.append, require=False)
        if (root / path).read_bytes() != before:
            notes.append(f'Formatted {path}; read it again before the next edit.')
    else:
        files = [path for path in changed(root) if language(path, root)]
        status = lint(files, root=root, out=report.append, require=False) if files else 0
    if status:
        notes += [line for line in report if not line.endswith((' clean', 'Nothing to lint.'))]
        notes.append('Fix these, or say why one stays (STYLE.md); `./bin/shaffy lint --fix PATH` formats.')
    if not notes:
        return {}
    text = '\n'.join(notes)
    if name == 'Stop':
        return {'decision': 'block', 'reason': text} if status else {}
    return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': text}}


def main(args):
    if args.hook:
        reply = hook(json.load(sys.stdin))
        if reply:
            print(json.dumps(reply))
        return 0
    if args.paths:
        named = [os.path.relpath(Path(p).resolve(), ROOT) for p in args.paths]
        everything = _files(ROOT)
        paths = [f for f in everything if any(f == n or f.startswith(n.rstrip('/') + '/') or n == '.' for n in named)]
    else:
        paths = changed() if args.changed else _files(ROOT)
    return run(paths, fix=args.fix)
