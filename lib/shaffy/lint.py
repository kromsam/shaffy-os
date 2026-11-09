"""`./bin/shaffy lint`: one formatter and linter per language, over the checkout (STYLE.md).

The tools come from the checkout's development tools (mise.dev.toml, found through `mise
env` with MISE_ENV=dev in the checkout), except fish_indent, which comes with fish. It lints
the files Git tracks or would track. Templates (`*.tmpl`, and all of
`home/.chezmoitemplates/`) get no formatter, which would not read their template syntax: the
sandboxed render checks what they produce. They, and every other text file no formatter
reads, are held to .editorconfig's [*] section by lint itself, which also parses JSON. The files REUSE.toml
attributes to another project are left out, and keep their upstream's form. Without --fix
nothing is written; with it the formatters and the linters' safe fixes rewrite the files first. Each tool's
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
import re
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
LINES = 40
INSTALL = 'mise trust mise.dev.toml and MISE_ENV=dev mise install, in the checkout'


@dataclass(frozen=True)
class Step:
    languages: tuple
    tool: str
    check: tuple
    # Arguments that rewrite the files, run first with --fix; None: the tool only checks.
    fix: tuple = None
    # Runs on the whole checkout, configured by its own file, rather than on the files given.
    project: bool = False
    # Comes with the system rather than mise.dev.toml.
    system: bool = False
    # The key in mise.dev.toml, where it is not the tool's own name (the opentofu package ships tofu).
    package: str = None


STEPS = (
    Step(('python',), 'ruff', ('check',), fix=('check', '--fix')),
    Step(('python',), 'ruff', ('format', '--check'), fix=('format',)),
    Step(('shell',), 'shfmt', ('--diff',), fix=('--write',)),
    Step(('shell',), 'shellcheck', ()),
    Step(('fish',), 'fish_indent', ('--check',), fix=('--write',), system=True),
    Step(('lua',), 'stylua', ('--check',), fix=()),
    Step(('markdown',), 'markdownlint-cli2', (), fix=('--fix',)),
    # ansible-lint runs yamllint on the playbooks itself, with the same .yamllint.
    Step(('ansible',), 'ansible-lint', (), project=True),
    Step(('yaml', 'workflow'), 'yamllint', ('--strict', '--format', 'parsable')),
    Step(('workflow',), 'actionlint', ()),
    Step(('workflow',), 'zizmor', ('--offline', '--no-progress')),
    Step(('toml',), 'taplo', ('fmt', '--check'), fix=('fmt',)),
    Step(('opentofu',), 'tofu', ('fmt', '-check', '-diff'), fix=('fmt',), package='opentofu'),
)

NAMED = {
    '.py': 'python',
    '.sh': 'shell',
    '.fish': 'fish',
    '.lua': 'lua',
    '.md': 'markdown',
    '.yml': 'yaml',
    '.yaml': 'yaml',
    '.toml': 'toml',
    '.tf': 'opentofu',
    '.json': 'json',
}


def language(path, root=ROOT):
    """The language a file is linted as: 'text' when no formatter reads it, None for a binary."""
    target = root / path
    if target.is_symlink() or not target.is_file():
        return None
    with open(target, 'rb') as file:
        head = file.read(8192)
    if b'\0' in head:
        return None
    if path.endswith('.tmpl') or path.startswith('home/.chezmoitemplates/'):
        return 'text'
    suffix = Path(path).suffix
    if path in ('site.yml', 'records.yml') or (path.startswith('roles/') and suffix == '.yml'):
        return 'ansible'
    if '/.github/workflows/' in '/' + path and suffix in ('.yml', '.yaml'):
        return 'workflow'
    # Before the suffix: chezmoi's modify_ scripts keep their target's name, such as
    # modify_settings.json.
    first = head.partition(b'\n')[0]
    if first.startswith((b'#!/usr/bin/python3', b'#!/usr/bin/env python3')):
        return 'python'
    if first.startswith((b'#!/bin/bash', b'#!/bin/sh', b'#!/usr/bin/env bash', b'#!/usr/bin/env sh')):
        return 'shell'
    if Path(path).name in ('.yamllint', '.ansible-lint'):
        return 'yaml'
    # The lock file ends in .hcl too, and tofu init owns it.
    if path.endswith('.tftest.hcl'):
        return 'opentofu'
    return NAMED.get(suffix, 'text')


# The system interpreter for each `env` shebang (STYLE.md): its Python has the packages
# Shaffy OS installs, where `env python3` may find mise's.
INTERPRETERS = {
    '#!/usr/bin/env python3': '#!/usr/bin/python3',
    '#!/usr/bin/env bash': '#!/bin/bash',
    '#!/usr/bin/env sh': '#!/bin/sh',
}


def editorconfig():
    """.editorconfig's [*] section: the settings every file shares."""
    section, settings = None, {}
    for line in (ROOT / '.editorconfig').read_text().splitlines():
        line = line.strip()
        if line.startswith('['):
            section = line
        elif section == '[*]' and '=' in line and not line.startswith('#'):
            key, value = (part.strip() for part in line.split('=', 1))
            settings[key] = value
    return settings


def whitespace(root, paths, fix=False):
    """The files that break .editorconfig's [*] section; --fix mends line ends, trailing spaces
    and a missing final newline, and reports the rest."""
    rules, found = editorconfig(), []
    for path in paths:
        try:
            text = (root / path).read_bytes().decode(rules.get('charset', 'utf-8'))
        except UnicodeDecodeError:
            found.append(f'{path}: not {rules.get("charset", "utf-8")}')
            continue
        mended = text.replace('\r\n', '\n') if rules.get('end_of_line') == 'lf' else text
        if rules.get('trim_trailing_whitespace') == 'true':
            mended = re.sub(r'[ \t]+$', '', mended, flags=re.MULTILINE)
        if rules.get('insert_final_newline') == 'true' and mended and not mended.endswith('\n'):
            mended += '\n'
        if fix and mended != text:
            (root / path).write_bytes(mended.encode(rules.get('charset', 'utf-8')))
            text = mended
        if rules.get('end_of_line') == 'lf' and '\r' in text:
            found.append(f'{path}: CR line ends; .editorconfig asks for LF')
        if rules.get('insert_final_newline') == 'true' and text and not text.endswith('\n'):
            found.append(f'{path}: no final newline')
        for number, line in enumerate(text.split('\n'), 1):
            if rules.get('trim_trailing_whitespace') == 'true' and line.rstrip('\r') != line.rstrip('\r').rstrip(' \t'):
                found.append(f'{path}:{number}: trailing whitespace')
            if rules.get('indent_style') == 'space' and line.startswith('\t'):
                found.append(f'{path}:{number}: indented with a tab')
    return found


# A JSON string, kept, or a JSONC comment, dropped.
JSONC = re.compile(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', re.DOTALL)


def parses(root, paths, fix=False):
    """The JSON files that do not parse, comments allowed as JSONC allows them."""
    found = []
    for path in paths:
        try:
            json.loads(JSONC.sub(lambda match: match.group(1) or '', (root / path).read_text()))
        except (UnicodeDecodeError, ValueError) as error:
            found.append(f'{path}: {error}')
    return found


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


# The checks shaffy lint makes itself, before the tools: the same in a supplement, which
# needs no configuration of its own.
CHECKS = (
    ('interpreter', ('python', 'shell'), interpreters),
    ('whitespace', ('text', 'json'), whitespace),
    ('json', ('json',), parses),
)


def options(tool):
    """This checkout's configuration for a tool, given explicitly so a supplement uses it too."""
    if tool == 'ruff':
        return ['--config', str(ROOT / 'ruff.toml')]
    if tool == 'stylua':
        return ['--config-path', str(ROOT / 'stylua.toml')]
    if tool == 'markdownlint-cli2':
        return ['--config', str(ROOT / '.markdownlint-cli2.yaml')]
    if tool == 'shellcheck':
        return ['--rcfile', str(ROOT / '.shellcheckrc')]
    if tool == 'yamllint':
        return ['--config-file', str(ROOT / '.yamllint')]
    if tool == 'zizmor':
        return ['--config', str(ROOT / '.github/zizmor.yml')]
    if tool == 'taplo':
        return ['--config', str(ROOT / '.taplo.toml')]
    if tool == 'shfmt':
        # As flags from .editorconfig's [*] section; with flags, shfmt reads no .editorconfig.
        settings = editorconfig()
        flags = ['--indent', settings.get('indent_size', '0')]
        flags += ['--case-indent'] if settings.get('switch_case_indent') == 'true' else []
        return flags + (['--binary-next-line'] if settings.get('binary_next_line') == 'true' else [])
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
    return [*lines[:LINES], f'… {len(lines) - LINES} more lines; run the tool on these files to see them all']


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
    for label, kinds, check in CHECKS:
        files = [p for kind in kinds for p in by_language.get(kind, [])]
        if not files:
            continue
        wrong = check(root, files, fix)
        out(f'{label}: {len(files)} file(s), problems remain' if wrong else f'{label}: {len(files)} file(s) clean')
        for line in _bounded('\n'.join(wrong)) if wrong else []:
            out(f'  {line}')
        failed = failed or bool(wrong)
    # Every fix first, then every check, so what is reported is the files as they end up.
    targets = {step: [p for kind in step.languages for p in by_language.get(kind, [])] for step in STEPS}
    steps = [step for step in STEPS if targets[step]]
    present = {step: is_available(step, root) for step in steps}
    for step in steps:
        if fix and step.fix is not None and present[step]:
            arguments = _command(step, step.fix, targets[step])
            call(arguments, cwd=root, env=_environment(), capture_output=True, text=True, check=False)
    for step in steps:
        files = targets[step]
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
