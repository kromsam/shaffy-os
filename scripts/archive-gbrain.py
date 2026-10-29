#!/usr/bin/python3
"""Owner-run, verified private snapshot before retiring GBrain. Dry-run by default.

Remove after 2027-01-16, with scripts/export-gbrain-snapshot.ts, tests/test_gbrain_archive.py
and docs/MIGRATING-AGENT-TOOLS.md.
"""

import argparse
from contextlib import contextmanager
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
SERVICE = 'gbrain-mcp.service'


def inventory(root):
    records = {}
    for path in sorted(root.rglob('*')):
        name = str(path.relative_to(root))
        if path.is_symlink():
            records[name] = {'symlink': os.readlink(path)}
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            records[name] = {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}
        elif path.is_dir():
            records[name] = {'directory': True}
        else:
            raise ValueError(f'Cannot archive special file: {name}')
    return records


def verify(destination):
    manifest = json.loads((destination / 'manifest.json').read_text())
    for section in ('raw', 'export'):
        if inventory(destination / section) != manifest[section]:
            raise ValueError(f'{section} archive verification failed')
    return manifest


@contextmanager
def exclusive_database(database):
    # The installed GBrain native addon uses flock on this stable inode.
    lock = Path(str(database.resolve()) + '.gbrain-owner.lock')
    with lock.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('GBrain still owns the database. Close all clients and retry.') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def paused_service():
    result = subprocess.run(['systemctl', '--user', 'is-active', SERVICE], capture_output=True, text=True)
    if result.returncode not in (0, 3, 4):
        raise ValueError('Cannot determine the GBrain service state')
    active = result.returncode == 0
    if active:
        subprocess.run(['systemctl', '--user', 'stop', SERVICE], check=True)
    try:
        yield
    finally:
        if active:
            subprocess.run(['systemctl', '--user', 'start', SERVICE], check=True)


def archive(home, destination):
    config = json.loads((home / '.gbrain/config.json').read_text())
    if config.get('engine') != 'pglite':
        raise ValueError('This recovery helper supports only a local PGLite database')
    database = Path(config['database_path']).expanduser().resolve()
    brain = home / '.gbrain'
    if (
        not (database / 'PG_VERSION').is_file()
        or not database.is_relative_to(brain.resolve())
        or not database.is_relative_to(home)
    ):
        raise ValueError('Expected an existing database inside .gbrain')
    if destination.exists():
        raise ValueError('Archive destination must not exist; partial archives are never reused')
    destination = destination.absolute()
    if destination.is_relative_to(home / '.gbrain') or destination.is_relative_to(home / '.gstack-brain-worktree'):
        raise ValueError('Archive destination must be outside the source data')
    # Private application data never belongs in a git checkout.
    for parent in (destination.parent, *destination.parents):
        if (parent / '.git').exists():
            raise ValueError('Use a private archive directory outside every git checkout')
    source = home / '.local/share/gbrain/source'
    if not (source / 'node_modules/@electric-sql/pglite/dist/index.js').exists():
        raise ValueError('Keep the installed GBrain source and dependencies until export finishes')
    os.umask(0o077)
    destination.mkdir(parents=True, mode=0o700)
    raw = destination / 'raw'
    raw.mkdir()
    paths = [
        '.gbrain',
        '.gstack-brain-worktree',
        '.config/gbrain-mcp',
        '.local/share/gbrain/source',
        '.local/bin/gbrain',
        '.local/bin/gbrain-mcp',
        '.local/lib/shaffy/gbrain-mcp-proxy.py',
        '.config/systemd/user/gbrain-mcp.service',
    ]
    with paused_service(), exclusive_database(database):
        for name in paths:
            original, target = home / name, raw / name
            if not original.exists() and not original.is_symlink():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if original.is_dir() and not original.is_symlink():
                shutil.copytree(original, target, symlinks=True)
            elif original.is_symlink():
                target.symlink_to(os.readlink(original))
            else:
                shutil.copy2(original, target)
        original_inventory = {
            name: inventory(home / name) for name in paths if (home / name).is_dir() and not (home / name).is_symlink()
        }
        for name, expected in original_inventory.items():
            if inventory(raw / name) != expected:
                raise ValueError(f'Source changed during snapshot: {name}')
    raw_inventory = inventory(raw)
    (destination / 'raw-checksums.json').write_text(json.dumps(raw_inventory, indent=2) + '\n')
    # Only the disposable copy is opened by PGLite. Raw remains byte-for-byte.
    working = destination / 'export-database'
    shutil.copytree(raw / database.relative_to(home), working)
    output = destination / 'export'
    output.mkdir()
    command = [
        '/usr/bin/mise',
        'exec',
        '--',
        'bun',
        str(REPO / 'scripts/export-gbrain-snapshot.ts'),
        str(raw / '.local/share/gbrain/source'),
        str(working),
        str(output),
    ]
    with (destination / 'export.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    if inventory(raw) != raw_inventory:
        raise ValueError('Export changed the raw snapshot')
    manifest = {
        'created_at': datetime.datetime.now(datetime.UTC).isoformat(),
        'raw': raw_inventory,
        'export': inventory(output),
    }
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    verify(destination)
    # Leaves original data, raw rollback snapshot and all export rows intact.
    shutil.rmtree(working)
    print(f'Private recovery archive verified: {destination}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-dir', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    destination = args.archive_dir.expanduser().resolve()
    if args.verify:
        verify(destination)
        print('Archive checksums verified.')
    elif args.apply:
        archive(Path.home(), destination)
    else:
        print(
            f'Dry run: snapshot private GBrain data and installed source to {destination}; '
            'pause and restore the user service; export every table from a disposable database copy. '
            'Close all agents first. Add --apply to perform the archive.'
        )


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'archive-gbrain: {error}')
