"""`./bin/shaffy lint`: one formatter and linter per language, over the checkout (STYLE.md).

The tools come from the checkout's development tools (mise.dev.toml, found through `mise
env` with MISE_ENV=dev in the checkout), except fish_indent, which comes with fish. It lints the
files Git tracks or would track. Templates (`*.tmpl`, and all of
`home/.chezmoitemplates/`) are left out: no formatter reads their template syntax, and
the sandboxed render checks what they produce. So are the files REUSE.toml attributes to
another project, which keep their upstream's form. Without --fix nothing is written;
with it the formatters and the linters' safe fixes rewrite the files first. Each tool's
output is cut to a bounded number of lines, and the exit status says whether anything
remains. Beside the tools, it holds every script to the system interpreter (STYLE.md),
and --fix rewrites an `env` shebang.

With --supplement it lints the bound supplement, or the one named, by the same rules:
every tool is given this checkout's configuration, so the supplement needs none of its
own. The home apply installs the agents' hooks for the supplement (`--hook
--supplement`), and the supplement's CI runs .github/workflows/supplement.yml.

`--hook` reads a Claude Code or Codex hook event on stdin and answers in their JSON.
At the session's start it records which files already differ from HEAD, so the owner's
unfinished work is never touched. After an edit to a file it fixes and checks that file;
after a tool that names no file, such as Codex's apply_patch, it does the same for every
file the session changed; it tells the agent which files it reformatted. When the agent
stops it checks the session's changed files once more, without writing, and holds the
stop back once while problems remain. A tool that is not installed is skipped there.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

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
    if first.startswith((b'#!/usr/bin/python3', b'#!/usr/bin/env python3')):
        return 'python'
    if first.startswith((b'#!/bin/bash', b'#!/bin/sh', b'#!/usr/bin/env bash', b'#!/usr/bin/env sh')):
        return 'shell'
    return None


# The system interpreter for each `env` shebang (STYLE.md): its Python has the packages
# Shaffy OS installs, where `env python3` may find mise's.
INTERPRETERS = {
    '#!/usr/bin/env python3': '#!/usr/bin/python3',
    '#!/usr/bin/env bash': '#!/bin/bash',
    '#!/usr/bin/env sh': '#!/bin/sh',
}


def interpreters(root, paths, fix=False):
    """The scripts whose shebang goes through `env`; --fix rewrites them instead."""
    found = []
    for path in paths:
        first, newline, rest = (root / path).read_text().partition('\n')
        wanted = INTERPRETERS.get(first.strip())
        if wanted and fix:
            (root / path).write_text(wanted + newline + rest)
        elif wanted:
            found.append(f'{path}:1: {first.strip()} should be {wanted}')
    return found


def options(tool):
    """This checkout's configuration for a tool, given explicitly so a supplement uses it too."""
    if tool == 'ruff':
        return ['--config', str(ROOT / 'ruff.toml')]
    if tool == 'stylua':
        return ['--config-path', str(ROOT / 'stylua.toml')]
    if tool == 'markdownlint-cli2':
        return ['--config', str(ROOT / '.markdownlint-cli2.yaml')]
    if tool == 'shfmt':
        # As flags from .editorconfig's [*] section; with flags, shfmt reads no .editorconfig.
        section, settings = None, {}
        for line in (ROOT / '.editorconfig').read_text().splitlines():
            line = line.strip()
            if line.startswith('['):
                section = line
            elif section == '[*]' and '=' in line and not line.startswith('#'):
                key, value = (part.strip() for part in line.split('=', 1))
                settings[key] = value
        flags = ['--indent', settings.get('indent_size', '0')]
        return flags + (['--case-indent'] if settings.get('switch_case_indent') == 'true' else [])
    return []


def third_party(root=ROOT):
    """The files REUSE.toml attributes to another project."""
    reuse = root / 'REUSE.toml'
    paths = set()
    for annotation in tomllib.loads(reuse.read_text())['annotations'] if reuse.exists() else []:
        listed = annotation['path']
        if listed != '**':
            paths.update([listed] if isinstance(listed, str) else listed)
    return paths


def _git(root, *arguments):
    """The paths a git listing prints, or None outside a Git checkout."""
    result = subprocess.run(['git', '-C', str(root), *arguments, '-z'], capture_output=True, text=True, check=False)
    return [path for path in result.stdout.split('\0') if path] if result.returncode == 0 else None


def files(root=ROOT):
    """The files Git tracks, and the untracked ones it does not ignore."""
    listed = _git(root, 'ls-files', '--cached', '--others', '--exclude-standard')
    if listed is None:
        raise SystemExit(f'shaffy lint: {root} is not a Git checkout Git can read')
    return sorted(set(listed))


def changed(root=ROOT):
    """Files that differ from HEAD, and untracked ones Git does not ignore."""
    return sorted(
        set(_git(root, 'diff', '--name-only', 'HEAD') or [])
        | set(_git(root, 'ls-files', '--others', '--exclude-standard') or [])
    )


_ENVIRONMENT = {}


def _environment():
    """The environment with the checkout's development tools on PATH, wherever a tool runs."""
    if not _ENVIRONMENT:
        _ENVIRONMENT.update(os.environ, MISE_ENV='dev')
        if shutil.which('mise'):
            listed = subprocess.run(
                ['mise', 'env', '--json'], cwd=ROOT, env=dict(_ENVIRONMENT), capture_output=True, text=True, check=False
            )
            if listed.returncode == 0:
                _ENVIRONMENT.update(json.loads(listed.stdout))
    return dict(_ENVIRONMENT)


def available(step, root=ROOT):
    return shutil.which(step.tool, path=_environment().get('PATH')) is not None


def _command(step, arguments, files):
    return [step.tool, *arguments, *options(step.tool), *([] if step.project else files)]


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
    scripts = by_language.get('python', []) + by_language.get('shell', [])
    wrong = interpreters(root, scripts, fix)
    if scripts:
        out(
            f'interpreter: {len(scripts)} file(s), problems remain'
            if wrong
            else f'interpreter: {len(scripts)} file(s) clean'
        )
        for line in wrong:
            out(f'  {line}')
        failed = bool(wrong)
    # Every fix first, then every check, so what is reported is the files as they end up.
    steps = [step for step in STEPS if by_language.get(step.language)]
    present = {step: is_available(step, root) for step in steps}
    for step in steps:
        if fix and step.fix is not None and present[step]:
            arguments = _command(step, step.fix, by_language[step.language])
            call(arguments, cwd=root, env=_environment(), capture_output=True, text=True, check=False)
    for step in steps:
        files = by_language[step.language]
        label = f'{step.tool} {step.check[0]}' if step.check and not step.check[0].startswith('-') else step.tool
        if not present[step]:
            if require:
                out(f'{label}: not installed; install it with {INSTALL}')
                failed = True
            continue
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


def _digest(root, path):
    return hashlib.sha256((root / path).read_bytes()).hexdigest()


def _state_file(event):
    """The session's record of what differed from HEAD when it started."""
    base = os.environ.get('XDG_RUNTIME_DIR') or os.path.expanduser('~/.cache')
    session = hashlib.sha256(str(event.get('session_id')).encode()).hexdigest()[:24]
    return Path(base) / 'shaffy/lint-sessions' / f'{session}.json'


def _save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state))
    temporary.replace(path)


def hook(event, root=ROOT, lint=run, state_file=None):
    """Claude Code or Codex hook: the JSON reply, or {} when there is nothing to say."""
    name = event.get('hook_event_name')
    state_file = state_file or _state_file(event)
    lintable = [path for path in changed(root) if language(path, root)]
    if name == 'SessionStart':
        # A resumed or compacted session keeps the record of its first start.
        if not (event.get('source') in ('resume', 'compact') and state_file.exists()):
            _save(state_file, {'start': {path: _digest(root, path) for path in lintable}, 'clean': {}})
        return {}
    if name == 'Stop' and event.get('stop_hook_active'):
        return {}
    state = json.loads(state_file.read_text()) if state_file.exists() else None
    # Without a record of the start, every changed file may be the owner's: check, never write.
    session = [p for p in lintable if state is None or _digest(root, p) != state['start'].get(p)]
    edited = (event.get('tool_input') or {}).get('file_path')
    if name == 'PostToolUse' and edited:
        path = os.path.relpath(Path(edited).resolve(), root)
        outside = path.startswith('..')
        if (
            outside
            or not language(path, root)
            or subprocess.run(['git', '-C', str(root), 'check-ignore', '-q', path], check=False).returncode == 0
        ):
            return {}
        targets, fix = [path], True
    elif name == 'PostToolUse':
        targets, fix = session, state is not None
    else:
        clean = state['clean'] if state else {}
        targets, fix = [p for p in session if clean.get(p) != _digest(root, p)], False
    if not targets:
        return {}
    before = {path: _digest(root, path) for path in targets}
    report = []
    status = lint(targets, fix=fix, root=root, out=report.append, require=False)
    notes = [f'Formatted {p}; read it again before the next edit.' for p in targets if _digest(root, p) != before[p]]
    if status:
        notes += [line for line in report if not line.endswith((' clean', 'Nothing to lint.'))]
        notes.append('Fix these, or say why one stays (STYLE.md); `./bin/shaffy lint --fix PATH` formats.')
    elif name == 'Stop' and state is not None:
        # A file checked clean is not checked again at the next stop until it changes.
        _save(state_file, dict(state, clean=dict(state['clean'], **before)))
    if not notes:
        return {}
    text = '\n'.join(notes)
    if name == 'Stop':
        return {'decision': 'block', 'reason': text} if status else {}
    return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': text}}


def supplement_root(path=None):
    """The named supplement, or the bound one; None when the installation has none."""
    from .selection import resolve

    selection = resolve(supplement=path or None)
    return Path(selection['path']) if selection['mode'] == 'supplement' else None


def main(args):
    root = ROOT
    if args.supplement is not None:
        root = supplement_root(args.supplement)
        if root is None and not args.hook:
            raise SystemExit('shaffy lint: no supplement is bound; name one with --supplement PATH')
    if args.hook:
        event = json.load(sys.stdin)
        cwd = Path(event.get('cwd') or os.getcwd()).resolve()
        # The home's hook for the supplement says nothing outside it.
        if root is None or (cwd != root and root not in cwd.parents):
            return 0
        reply = hook(event, root=root)
        if reply:
            print(json.dumps(reply))
        return 0
    everything = files(root)
    if args.paths:
        named = [os.path.relpath(Path(p).resolve(), root) for p in args.paths]
        paths = [f for f in everything if any(f == n or f.startswith(n.rstrip('/') + '/') or n == '.' for n in named)]
    else:
        paths = changed(root) if args.changed else everything
    return run(paths, fix=args.fix, root=root)
