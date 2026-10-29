#!/usr/bin/python3
"""Move an existing Zotero profile to the managed name, so the `zotero` integration can adopt it.

Zotero names a new profile directory at random (`abcd1234.default`); Shaffy OS
manages `~/.zotero/zotero/shaffy.default`. This renames the one existing profile
directory and points `profiles.ini` at it; nothing inside the profile changes,
and the library in the data directory is not touched. Without `--apply` it only
prints the plan.

Zotero must be closed: its running instance holds `.parentlock`. A snapper
pre/post pair (config home) brackets the move, the old `profiles.ini` is copied
first, and the output says how to undo it. A run interrupted between the rename
and the `profiles.ini` write is finished by running it again.
"""

import argparse
import configparser
import fcntl
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from shaffy.snapshots import SnapshotError, home_pair

MANAGED = 'shaffy.default'


class MoveError(Exception):
    pass


def profile(ini):
    """(section, path) of the single relative profile in profiles.ini."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(ini.read_text())
    sections = [s for s in parser.sections() if s.startswith('Profile')]
    if len(sections) != 1:
        raise MoveError(f'{ini} lists {len(sections)} profiles; move them by hand')
    section = parser[sections[0]]
    if section.get('IsRelative') != '1' or not section.get('Path'):
        raise MoveError(f'{ini}: the profile is not a relative path; move it by hand')
    return sections[0], section['Path']


def running(directory):
    """Whether a Zotero holds the profile's lock."""
    lock = directory / '.parentlock'
    if not lock.exists():
        return False
    with open(lock, 'rb') as stream:
        try:
            fcntl.lockf(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.lockf(stream, fcntl.LOCK_UN)
    return False


def rewrite(ini, old, new):
    """Point the profile's Path line at the new name, changing nothing else."""
    lines = ini.read_text().splitlines(keepends=True)
    changed = [
        line.replace(f'Path={old}', f'Path={new}', 1) if line.strip() == f'Path={old}' else line for line in lines
    ]
    handle, temporary = tempfile.mkstemp(dir=ini.parent, prefix='.profiles-', suffix='.tmp')
    with os.fdopen(handle, 'w') as stream:
        stream.write(''.join(changed))
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, ini.stat().st_mode & 0o777)
    os.replace(temporary, ini)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--apply', action='store_true', help='move the profile; without it, print the plan')
    parser.add_argument('--home', type=Path, default=Path.home(), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = args.home / '.zotero/zotero'
    ini = root / 'profiles.ini'
    try:
        if not ini.exists():
            print('There is no Zotero profile yet; the home apply creates the managed one.')
            return 0
        _, current = profile(ini)
        old, new = root / current, root / MANAGED
        if current == MANAGED:
            print(f'The profile is already {MANAGED}; run ./bin/shaffy integration zotero adopt.')
            return 0
        interrupted = not old.exists() and new.is_dir()
        if not interrupted:
            if not old.is_dir():
                raise MoveError(f'{ini} names {current}, which does not exist')
            if os.path.lexists(new):
                raise MoveError(f'{new} already exists; review both profiles by hand')
            if running(old):
                raise MoveError('Zotero is running; close it and run this again')
        plan = (
            f'finish the interrupted move: point {ini} at {MANAGED}'
            if interrupted
            else f'rename {old} to {new} and point {ini} at it'
        )
        if not args.apply:
            print(f'Would {plan}. Close Zotero and add --apply to do it.')
            return 0
        state = Path(os.environ.get('XDG_STATE_HOME') or args.home / '.local/state')
        copies = state / 'shaffy/runs' / time.strftime('%Y%m%dT%H%M%SZ-zotero-profile', time.gmtime())
        with home_pair('move the Zotero profile to shaffy.default'):
            copies.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(ini, copies / 'profiles.ini')
            if not interrupted:
                os.rename(old, new)
            rewrite(ini, current, MANAGED)
    except (MoveError, SnapshotError, OSError, configparser.Error) as error:
        print(f'move-zotero-profile: {error}', file=sys.stderr)
        return 1
    print(
        f'Moved the profile to {new}. The old profiles.ini is in {copies}.\n'
        f'Undo, with Zotero closed: mv {new} {old}; cp {copies}/profiles.ini {ini}\n'
        'Next: start Zotero once to check the library, then run ./bin/shaffy integration zotero adopt.'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
