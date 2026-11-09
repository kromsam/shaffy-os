"""Freeze the supplement inputs of one operation (docs/ARCHITECTURE.md, "Capture").

The capture copies the supplement's config.yaml, the files it references and
the files of its dotfiles folder into a private run directory, refuses a
supplement that changes while it is copied, and records per-file SHA-256
digests plus a digest of the normalized settings. chezmoi then reads the
capture through the loader marker, so an edit made during an apply cannot mix
old and new settings.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil

TREE_FILES = 10_000
TREE_BYTES = 64 * 1024 * 1024  # every capture copies the dotfiles, and the newest ten runs are kept


class CaptureError(Exception):
    """The supplement changed during capture or the capture does not validate."""


def runs_dir(state):
    return Path(state) / 'runs'


def settings_digest(settings):
    """The digest of the normalized settings, recorded at capture and compared before writing."""
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()


def _digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _manifest(root, files):
    return {name: _digest(Path(root) / name) for name in files}


def tree(root, boundary):
    """The files of the dotfiles folder ROOT, relative to it, as a capture copies them.

    `.git`, a clone's folder or a submodule's file, is left out; chezmoi ignores it. A link is
    taken as the content of the regular file it leads to inside BOUNDARY, the supplement, as a
    reference is. Raises CaptureError for any other link or special file, beyond the limits, and
    for a folder without files, which is most often a Git submodule nobody initialised.
    """
    root, boundary = Path(os.path.realpath(root)), os.path.realpath(boundary)

    def unreadable(error):
        raise CaptureError(f'the dotfiles cannot be read: {error}')

    names, size = [], 0
    for folder, folders, files in root.walk(on_error=unreadable):
        folders[:] = [name for name in folders if name != '.git']
        for path in (folder / name for name in files if name != '.git'):
            name, resolved = str(path.relative_to(root)), os.path.realpath(path)
            if os.path.commonpath([boundary, resolved]) != boundary:
                raise CaptureError(f"the dotfiles' {name} leads outside the supplement; copy the file in instead")
            if not os.path.isfile(resolved):
                raise CaptureError(f"the dotfiles' {name} is neither a regular file nor a link to one")
            names.append(name)
            size += os.path.getsize(resolved)
            if len(names) > TREE_FILES:
                raise CaptureError(f'the dotfiles hold more than {TREE_FILES} files, more than a capture copies')
            if size > TREE_BYTES:
                raise CaptureError(f'the dotfiles hold more than {TREE_BYTES // 2**20} MiB, more than a capture copies')
    if not names:
        raise CaptureError(
            'the dotfiles folder holds no files; if it is a Git submodule, '
            'run git submodule update --init in the supplement'
        )
    return sorted(names)


def _files(source, shaffy, dotfiles):
    """What a capture copies from the supplement SOURCE, under the paths config.yaml names; with DOTFILES, their files."""
    reference = shaffy['dotfiles'] if dotfiles else None
    dotfiles = [f'{reference}/{name}' for name in tree(source / reference, source)] if reference else []
    return list(dict.fromkeys(['config.yaml', *shaffy['reference_paths'], *dotfiles]))


def capture(checkout, selection, state, op, load, dotfiles=False):
    """Capture into <state>/runs/<op>/capture and return its manifest.

    DOTFILES takes the dotfiles' files too, for the home and its previews; a system
    operation never reads them, so its capture, and root's copy of it, holds only their
    folder, empty. A capture that fails is removed, so no copy of the private settings is
    left behind without a record.
    """
    old_umask = os.umask(0o077)
    run = runs_dir(state) / op
    try:
        directory = run / 'capture'
        directory.mkdir(parents=True)
        (directory / 'selection.json').write_text(json.dumps(selection, sort_keys=True) + '\n')
        files = []
        if selection['mode'] == 'supplement':
            source = Path(selection['path'])
            live = load(checkout, supplement=source)
            # Referenced files are captured under the paths config.yaml names, with the content
            # they resolve to, so a reference through a symlink reads the same from the capture.
            files = _files(source, live['shaffy'], dotfiles)
            before = _manifest(source, files)
            for name in files:
                (directory / 'supplement' / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / name, directory / 'supplement' / name)
            if (
                _files(source, live['shaffy'], dotfiles) != files
                or _manifest(source, files) != before
                or _manifest(directory / 'supplement', files) != before
            ):
                raise CaptureError(
                    f'the supplement at {source} changed while it was captured; '
                    'finish editing and run the command again'
                )
            if live['shaffy']['dotfiles'] and not dotfiles:
                # The settings name the folder, so the captured settings must find it.
                (directory / 'supplement' / live['shaffy']['dotfiles']).mkdir(parents=True, exist_ok=True)
        data = load(checkout, supplement=directory / 'supplement' if files else None, without=not files)
        # The loaded settings, which a system operation's root copy verifies and its play reads.
        (directory / 'settings.json').write_text(json.dumps(data, sort_keys=True) + '\n')
        captured = ['selection.json', 'settings.json'] + [f'supplement/{name}' for name in files]
        manifest = {
            'op': op,
            'files': _manifest(directory, captured),
            'settings_sha256': settings_digest(data['shaffy']['settings']),
            'supplement_id': data['shaffy']['selection']['supplement_id'],
        }
        (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        return {**manifest, 'directory': str(directory)}
    except BaseException:
        shutil.rmtree(run, ignore_errors=True)
        raise
    finally:
        os.umask(old_umask)


def verify(directory):
    """Recompute a capture's file digests; raise CaptureError on any difference.

    Every file in the capture must be in its manifest, selection.json included.
    """
    directory = Path(directory)
    try:
        manifest = json.loads((directory / 'manifest.json').read_text())
        present = sorted(
            str(p.relative_to(directory))
            for p in directory.rglob('*')
            if not p.is_dir() and p.relative_to(directory) != Path('manifest.json')
        )
        if present != sorted(manifest['files']):
            raise CaptureError(f'the capture in {directory} holds other files than its manifest lists')
        actual = _manifest(directory, present)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CaptureError(f'the capture in {directory} is missing or unreadable: {error}') from error
    if actual != manifest['files']:
        raise CaptureError(f'the capture in {directory} no longer matches its manifest')
    return manifest
