"""The framework source manifest: what the applying checkout contained.

sha256 over the sorted (path, content sha256) pairs of the tracked and
untracked-but-not-ignored files, plus HEAD and a dirty flag. A capture freezes
the supplement; this detects changes to the framework itself during an operation.
"""
import hashlib
import os
from pathlib import Path
import subprocess

SKIP = {'.git', '__pycache__', 'artifacts'}


def _files(checkout):
    listing = subprocess.run(['git', '-C', str(checkout), 'ls-files', '-co', '--exclude-standard', '-z'],
                             capture_output=True, check=False)
    if listing.returncode == 0:
        return sorted(p for p in listing.stdout.decode().split('\0') if p)
    found = []
    for directory, names, files in os.walk(checkout):
        names[:] = [n for n in names if n not in SKIP]
        found += [os.path.relpath(os.path.join(directory, f), checkout) for f in files]
    return sorted(found)


def manifest(checkout):
    checkout = Path(checkout)
    digest = hashlib.sha256()
    for relative in _files(checkout):
        path = checkout / relative
        digest.update(relative.encode() + b'\0')
        if path.is_symlink():
            digest.update(b'link:' + os.readlink(path).encode())
        elif path.is_file():
            with open(path, 'rb') as stream:
                digest.update(hashlib.file_digest(stream, 'sha256').digest())
    head = subprocess.run(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], capture_output=True,
                          text=True, check=False).stdout.strip() or None
    dirty = bool(subprocess.run(['git', '-C', str(checkout), 'status', '--porcelain'], capture_output=True,
                                text=True, check=False).stdout.strip())
    return {'sha256': digest.hexdigest(), 'head': head, 'dirty': dirty}
