#!/usr/bin/env python3
"""Promote the encrypted default keyring to the `login` collection before its daemon first starts.

GNOME Keyring's PAM module unlocks only the collection whose file is
login.keyring; a keyring made under another name (a localized default such as
"Standaard sleutelbos") stays locked. This renames that one encrypted file,
keeping its bytes, after a hash-verified private copy, and points `default` at
it. It never decrypts anything and refuses whenever the directory is not in the
expected shape.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import tempfile

HEADER = b'GnomeKeyring\n\r\0\n'


def regular_owned(path):
    if path.resolve() != path:
        raise RuntimeError('Refusing linked keyring or state paths')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Expected a regular file owned by the current user')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inspect(directory):
    """The name of the collection to promote, or None when `login` is already the default."""
    if directory.resolve() != directory or directory.stat().st_uid != os.getuid():
        raise RuntimeError('Unexpected keyring directory')
    alias = directory / 'default'
    regular_owned(alias)
    name = alias.read_text().strip()
    new = directory / 'login.keyring'
    if name == 'login' and new.is_file():
        regular_owned(new)
        with new.open('rb') as stream:
            if stream.read(len(HEADER)) != HEADER:
                raise RuntimeError('The login keyring must remain encrypted')
        return None
    old = directory / (name + '.keyring')
    if not re.fullmatch(r'[^/.][^/]*', name) or os.path.lexists(new) or not os.path.lexists(old):
        raise RuntimeError('An existing login keyring or unexpected default requires review')
    regular_owned(old)
    if stat.S_IMODE(old.stat().st_mode) != 0o600:
        raise RuntimeError('Expected private keyring file permissions')
    with old.open('rb') as stream:
        if stream.read(len(HEADER)) != HEADER:
            raise RuntimeError('Refusing a plaintext or unrecognized keyring')
    for path in directory.iterdir():
        regular_owned(path)
    return name


def require_daemon_stopped():
    for process in Path('/proc').glob('[0-9]*'):
        try:
            if process.stat().st_uid == os.getuid() and (process / 'comm').read_text().strip().startswith('gnome-keyring'):
                raise RuntimeError('Keyring daemon is running; migration is only for pre-login')
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue


def promote(directory, state):
    """Caller must keep the daemon stopped. Preserve original encrypted bytes."""
    name = inspect(directory)
    if name is None:
        return {'changed': False}
    if state.resolve() != state:
        raise RuntimeError('Refusing linked recovery state')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=state))
    before = backup / 'before'
    shutil.copytree(directory, before, copy_function=shutil.copy2)
    hashes = {p.name: digest(p) for p in directory.iterdir()}
    if hashes != {p.name: digest(p) for p in before.iterdir()}:
        raise RuntimeError('Keyring backup verification failed')
    old, new = directory / (name + '.keyring'), directory / 'login.keyring'
    alias = directory / 'default'
    old_alias = alias.read_bytes()
    # link() refuses an existing destination. With the daemon stopped there is
    # no writer; remove the old directory entry only after verification.
    os.link(old, new)
    try:
        if digest(new) != hashes[old.name]:
            raise RuntimeError('Encrypted keyring changed unexpectedly')
        old.unlink()
        fd, temporary = tempfile.mkstemp(prefix='.default-', dir=directory)
        temp = Path(temporary)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(b'login\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, alias)
        finally:
            temp.unlink(missing_ok=True)
    except BaseException:
        if not os.path.lexists(old):
            os.link(new, old)
        new.unlink()
        alias.write_bytes(old_alias)
        raise
    report = {'changed': True, 'backup': str(before), 'encrypted_sha256': digest(new),
              'bytes_preserved': digest(new) == hashes[old.name]}
    (backup / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    handle = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    account = pwd.getpwuid(os.getuid())
    if os.getuid() == 0 or Path.home() != Path(account.pw_dir):
        raise RuntimeError("Run as the keyring's owner, with HOME set to that account's home")
    os.umask(0o077)
    directory = Path.home() / '.local/share/keyrings'
    state = Path.home() / '.local/state/shaffy/login-keyring'
    needed = inspect(directory)
    if not args.apply or not needed:
        print(json.dumps({'migration_needed': needed is not None, 'changed': False}))
        return
    require_daemon_stopped()
    print(json.dumps(promote(directory, state)))


if __name__ == '__main__':
    main()
