"""Which settings apply: explicit choice, recorded binding, discovery or defaults.

Checked in that order (docs/ARCHITECTURE.md, "Selection and binding"). Standard
library only, because the cached settings path imports it on every chezmoi
template execution.
"""
import json
import os
from pathlib import Path


class SelectionError(Exception):
    """The selected supplement is missing, malformed or contradictory."""


def state_dir(home=None):
    home = Path(home) if home else Path.home()
    return Path(os.environ.get('XDG_STATE_HOME') or home / '.local/state') / 'shaffy'


READ_ONLY_COMMANDS = ('diff', 'status', 'cat', 'verify', 'dump', 'managed', 'execute-template')


def _from_marker(mode, home):
    """During a Shaffy OS operation, chezmoi reads the operation's frozen capture."""
    op, capture = os.environ.get('SHAFFY_OP'), os.environ.get('SHAFFY_OP_CAPTURE')
    if not op or not capture:
        raise SelectionError('the Shaffy OS operation marker is incomplete; run through ./bin/shaffy')
    capture = Path(capture)
    if mode == 'apply':
        try:
            pending = json.loads((state_dir(home) / 'binding.json').read_text())['pending']
        except (OSError, ValueError, KeyError, TypeError):
            pending = None
        if not pending or pending.get('op') != op or pending.get('capture') != str(capture):
            raise SelectionError(f'operation {op} is not the unfinished operation on record; '
                                 'run through ./bin/shaffy')
    elif os.environ.get('CHEZMOI_COMMAND', 'diff') not in READ_ONLY_COMMANDS:
        raise SelectionError('a Shaffy OS preview may only be read, never applied')
    try:
        selection = json.loads((capture / 'selection.json').read_text())
    except (OSError, ValueError) as error:
        raise SelectionError(f'the capture {capture} is unreadable: {error}') from error
    if selection['mode'] == 'none':
        return {'mode': 'none', 'path': None, 'source': 'capture'}
    return {'mode': 'supplement', 'path': str(capture / 'supplement'), 'source': 'capture'}


def resolve(checkout, supplement=None, without=False, home=None):
    """Return {'mode': 'supplement'|'none', 'path', 'source'} or raise SelectionError."""
    checkout = Path(checkout)
    mode = os.environ.get('SHAFFY_OP_MODE')
    if mode in ('apply', 'preview') and not supplement and not without:
        return _from_marker(mode, home)
    if supplement and without:
        raise SelectionError('--supplement and --without-supplement exclude each other')
    if without:
        return {'mode': 'none', 'path': None, 'source': 'explicit'}
    if supplement:
        return _supplement(Path(supplement).expanduser(), 'explicit')
    binding = state_dir(home) / 'binding.json'
    if binding.exists():
        try:
            record = json.loads(binding.read_text())['binding']
            record['mode']
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise SelectionError(f'the recorded binding {binding} is unreadable: {error}') from error
        return bound_supplement(record)
    nested = checkout / 'supplement'
    if os.path.lexists(nested):
        return _supplement(nested, 'discovered')
    return {'mode': 'none', 'path': None, 'source': 'default'}


def bound_supplement(binding):
    """The recorded binding as a selection; a moved supplement names the rebind command."""
    mode = binding['mode']
    if mode == 'none':
        return {'mode': 'none', 'path': None, 'source': 'binding'}
    if mode != 'supplement' or not binding.get('path'):
        raise SelectionError(f'the recorded binding has an unknown mode {mode!r}')
    try:
        return _supplement(Path(binding['path']), 'binding')
    except SelectionError as error:
        raise SelectionError(f'{error}; if it moved, run ./bin/shaffy supplement rebind '
                             '--path NEW_PATH') from error


def _supplement(path, source):
    label = {'explicit': 'the selected', 'binding': 'the bound', 'discovered': 'the discovered'}[source]
    if not path.exists():
        dangling = ' (a dangling symlink)' if os.path.islink(path) else ''
        raise SelectionError(f'{label} supplement {path} does not exist{dangling}')
    root = Path(os.path.realpath(path))
    if not root.is_dir():
        raise SelectionError(f'{label} supplement {path} is not a directory')
    if not (root / 'config.yaml').is_file():
        raise SelectionError(f'{label} supplement {path} has no config.yaml')
    if not os.access(root / 'config.yaml', os.R_OK):
        raise SelectionError(f'{label} supplement {path} has an unreadable config.yaml')
    return {'mode': 'supplement', 'path': str(root), 'source': source}
