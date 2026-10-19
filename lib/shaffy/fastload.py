"""Cached settings for chezmoi templates and helpers (docs/ARCHITECTURE.md, "The cached path").

A cache hit uses the standard library only: run as
`/usr/bin/python3 -I -S lib/shaffy/fastload.py`, it prints the settings JSON in
well under 25 ms. A miss enables site-packages and runs the full loader.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shaffy.selection import SelectionError, resolve  # noqa: E402

CACHE_VERSION = '2'
CACHE_KEEP = 32
CODE = ('loader.py', 'selection.py', 'fastload.py', 'packages.py', 'integrations.py', 'source.py')


def cache_dir():
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if runtime and os.path.isdir(runtime):
        return Path(runtime) / 'shaffy/loader'
    return Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'shaffy/loader'


def _digest(checkout, selection):
    digest = hashlib.sha256(CACHE_VERSION.encode())
    inputs = [checkout / 'defaults/config.yaml', checkout / 'schema/supplement.schema.json']
    inputs += sorted((checkout / 'defaults/packages').glob('*.yml'))
    inputs += [path for path in [checkout / 'defaults/integrations.yml'] if path.exists()]
    inputs += [checkout / 'lib/shaffy' / name for name in CODE]
    if selection['mode'] == 'supplement':
        inputs.append(Path(selection['path']) / 'config.yaml')
    digest.update(json.dumps(selection, sort_keys=True).encode())
    for path in inputs:
        digest.update(str(path).encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


def _stamps(references):
    stamps = []
    for reference in references:
        status = os.stat(reference)
        stamps.append([reference, status.st_size, status.st_mtime_ns, status.st_ino, status.st_ctime_ns])
    return stamps


def _still_resolves(shaffy):
    """Every reference as config.yaml writes it still leads to the file the cache recorded."""
    root = shaffy['selection'].get('path')
    if not root:
        return not shaffy['references']
    resolved = sorted({os.path.realpath(os.path.join(root, path)) for path in shaffy['reference_paths']})
    return resolved == sorted(shaffy['references'])


def _store(directory, entry, payload):
    import tempfile  # only a miss stores; the import costs a cache hit ~4 ms
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary = tempfile.mkstemp(dir=directory, prefix='.entry-')
    with os.fdopen(handle, 'w') as stream:
        json.dump(payload, stream)
    os.replace(temporary, entry)
    entries = sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for stale in entries[CACHE_KEEP:]:
        stale.unlink(missing_ok=True)


def load_data(checkout, supplement=None, without=False, home=None, directory=None):
    """Return the normalized settings, validating fully only when inputs changed."""
    checkout = Path(checkout).resolve()
    selection = resolve(checkout, supplement, without, home)
    directory = Path(directory) if directory else cache_dir()
    key = _digest(checkout, selection)
    entry = directory / f'{key}.json'
    try:
        cached = json.loads(entry.read_text())
        if _still_resolves(cached['data']['shaffy']) and _stamps(cached['data']['shaffy']['references']) == cached['stamps']:
            return cached['data']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if sys.flags.no_site:
        import site
        site.main()
    from shaffy.loader import load
    data = load(checkout, supplement, without, home)
    # An input edited during the load would store new settings under the old key.
    if _digest(checkout, selection) == key:
        _store(directory, entry, {'data': data, 'stamps': _stamps(data['shaffy']['references'])})
    return data


def main(argv):
    checkout, supplement, without = Path(__file__).resolve().parents[2], None, False
    arguments = iter(argv)
    for argument in arguments:
        if argument == '--checkout':
            checkout = Path(next(arguments))
        elif argument == '--supplement':
            supplement = next(arguments)
        elif argument == '--without-supplement':
            without = True
        else:
            print(f'shaffy: unknown argument {argument!r}', file=sys.stderr)
            return 2
    try:
        data = load_data(checkout, supplement, without)
    except SelectionError as error:
        print(f'shaffy: {error}', file=sys.stderr)
        return 1
    except Exception as error:  # SettingsError lives in the lazily imported loader
        if type(error).__name__ != 'SettingsError':
            raise
        print('shaffy: invalid settings:\n' + str(error), file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(data, sort_keys=True) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
