#!/usr/bin/python3
"""Raise this repository's pins to their newest usable upstream versions, on dev.

Run it in the dev worktree, read the diff, run ./bin/shaffy selftest and commit; CI tests
the new pins and a release takes them to the machine (docs/UPDATING.md). It changes only
files in this checkout. A pin it cannot raise is reported and left alone.
"""

import argparse
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))

from shaffy import pins


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true', help='report the pins only; change nothing')
    parser.add_argument('--lazyvim', action='store_true', help='refresh the LazyVim plugin lock even when it is recent')
    args = parser.parse_args(argv)
    branch = subprocess.run(
        ['git', '-C', str(REPO), 'branch', '--show-current'], capture_output=True, text=True, check=False
    ).stdout.strip()
    if branch == 'main' and not args.check:
        sys.exit('refresh-pins: main is the released version; refresh the pins on dev')
    failed = False
    for pin in pins.check(REPO):
        if pin.error:
            print(f'{pin.name}: not checked: {pin.error}')
            failed = True
            continue
        due = pin.behind or (args.lazyvim and pin.name == 'LazyVim plugin lock')
        if not due:
            print(f'{pin.name}: {pin.current}, up to date')
        elif args.check or not pin.raise_to:
            print(f'{pin.name}: {pin.current} → {pin.latest}' + (f'; {pin.note}' if pin.note else ''))
        else:
            try:
                changed = pin.raise_to()
            except Exception as error:  # keep the other pins; this one stays as it was
                print(f'{pin.name}: not raised: {type(error).__name__}: {error}')
                failed = True
                continue
            moved = f'{pin.current} → {pin.latest}' if pin.latest != pin.current else 'refreshed'
            print(f'{pin.name}: {moved}' + (f' ({", ".join(changed)})' if changed else ', nothing changed'))
    if not args.check:
        print('Review `git diff`, run ./bin/shaffy selftest, then commit on dev.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
