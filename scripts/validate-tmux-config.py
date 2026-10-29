#!/usr/bin/python3
"""Source a tmux configuration on a private server; tmux reports parse errors on stderr but exits 0."""

import os
from pathlib import Path
import subprocess
import sys
import uuid


def validate(path):
    socket = f'validate-{uuid.uuid4().hex[:12]}'
    command = [
        'tmux',
        '-L',
        socket,
        '-f',
        '/dev/null',
        'start-server',
        ';',
        'source-file',
        str(Path(path).resolve()),
        ';',
        'kill-server',
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False, env={**os.environ, 'TMUX': ''})
    # tmux reports source-file errors on stdout of the client and still exits 0.
    return (result.stdout + result.stderr).strip() + (
        '' if result.returncode == 0 else f'\ntmux exited {result.returncode}'
    )


def main():
    errors = validate(sys.argv[1])
    if errors:
        sys.exit(errors)
    print(f'{sys.argv[1]}: tmux configuration sourced without errors')


if __name__ == '__main__':
    main()
