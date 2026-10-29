"""Validate a rendered home with each application's own checker."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

ANSI = re.compile(r'\x1b\[[0-9;]*m')
# Noctalia fetches plugins from its sources at runtime, so a rendered home has
# none: their settings and their widgets (types written author/plugin:entry) are expected
# to be unknown there. Built-in widgets and everything else must still validate.
NOCTALIA_EXPECTED = re.compile(
    r'plugin_settings\.[^:\s]+: no loaded plugin with this id'
    r'|widget\.[^:\s]+: unrecognized widget type "[^"/\s]+/[^"\s]+"'
)
SKIP_DIRS = {'.cache', '__pycache__'}


def _run(command, timeout=60):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    return result.returncode, ANSI.sub('', result.stdout + result.stderr).strip()


def _tail(text, lines=8):
    return '\n'.join(text.splitlines()[-lines:])


def validate_desktop(home):
    """Niri and Noctalia, checked by the applications themselves."""
    home = Path(home)
    problems = []
    niri = home / '.config/niri/config.kdl'
    if niri.exists():
        code, output = _run(['niri', 'validate', '-c', str(niri)])
        if code:
            problems.append('niri: ' + _tail(output))
    noctalia = home / '.config/noctalia'
    if noctalia.is_dir():
        code, output = _run(['noctalia', 'config', 'validate', str(noctalia)])
        unexpected = [
            line for line in output.splitlines() if re.search(r'\bWARN', line) and not NOCTALIA_EXPECTED.search(line)
        ]
        if code or unexpected:
            problems.append('noctalia: ' + ('\n'.join(unexpected) or _tail(output)))
    return problems


def validate_applied(home):
    """The real home after an apply: the desktop, and Alacritty with its generated imports."""
    home = Path(home)
    problems = validate_desktop(home)
    for path in sorted((home / '.config/alacritty').rglob('*.toml')):
        try:
            tomllib.loads(path.read_text())
        except (OSError, ValueError) as error:
            problems.append(f'{path.relative_to(home)}: {type(error).__name__}: {error}')
    return problems


def validate_home(home, checkout):
    """A rendered home: the desktop, Fish, tmux and every TOML, JSON and YAML file."""
    home, checkout = Path(home), Path(checkout)
    problems = validate_desktop(home)
    for script in sorted((home / '.config/fish').rglob('*.fish')):
        code, output = _run(['fish', '--no-execute', str(script)])
        if code:
            problems.append(f'fish: {script.relative_to(home)}: {_tail(output, 3)}')
    tmux = home / '.config/tmux/tmux.conf'
    if tmux.exists() and shutil.which('tmux'):
        code, output = _run(['/usr/bin/python3', str(checkout / 'scripts/validate-tmux-config.py'), str(tmux)])
        if code:
            problems.append('tmux: ' + _tail(output))
    for path in sorted(home.rglob('*')):
        if SKIP_DIRS & set(path.relative_to(home).parts) or not path.is_file() or path.is_symlink():
            continue
        try:
            if path.suffix == '.toml':
                tomllib.loads(path.read_text())
            elif path.suffix == '.json':
                json.loads(path.read_text())
            elif path.suffix in ('.yaml', '.yml'):
                import yaml

                yaml.safe_load(path.read_text())
        except Exception as error:  # report any parser's error with the file
            problems.append(f'{path.relative_to(home)}: {type(error).__name__}: {error}')
    return problems
