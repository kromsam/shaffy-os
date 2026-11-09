"""The profile commands' writes to a supplement's profiles/ folder (docs/PROFILES.md).

The only code that writes profiles/: the vendored folders and profiles/lock.json. It writes
nothing else: never config.yaml, never Git's index, never a commit. The commands that fetch
(add, update, propose) and the reviews come later and build on install. fastload does not hash
this module, so a change here never invalidates a settings cache.
"""

import json
import os
from pathlib import Path
import shutil
import tempfile

from . import profiles


class ProfileError(Exception):
    """A profile command cannot go on; nothing was written, or the rerun converges."""


def install(root, profile_id, files, locked):
    """Make profiles/OWNER/NAME hold exactly FILES and record LOCKED; add, update, profile check's
    temporary supplements and the tests' fixtures are all built with this."""
    owner, name = profiles.parse_id(profile_id)
    folder = Path(root) / profiles.FOLDER / owner / name
    if folder.exists():
        shutil.rmtree(folder)
    for relative, content in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    lock, problems = profiles.read_lock(root)
    if problems:
        raise ProfileError('\n'.join(problems))
    lock[profile_id] = locked
    _write_lock(root, lock)


def _write_lock(root, lock):
    """Sorted, indented JSON with a final newline, through a temporary file and os.replace."""
    path = Path(root) / profiles.LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        'lock_version': profiles.LOCK_VERSION,
        'profiles': {
            profile_id: {
                'commit': entry.commit,
                'files': dict(sorted(entry.files.items())),
                'path': entry.path,
                'ref': entry.ref,
                'source': entry.source,
            }
            for profile_id, entry in sorted(lock.items())
        },
    }
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix='.lock-')
    with os.fdopen(handle, 'w') as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write('\n')
    os.replace(temporary, path)
