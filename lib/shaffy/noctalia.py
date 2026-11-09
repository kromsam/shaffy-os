"""Noctalia keys set by a managed fragment and again by app-owned runtime files.

Noctalia reads every *.toml in its config directory alphabetically, so the
unmanaged config.toml and the create-only 99-runtime.toml load after the managed
90-97 fragments, and its state settings.toml loads last of all. A key set in both
places silently takes the runtime value. `shaffy check` reports each such key so
it can be promoted into a fragment or cleared deliberately, except the clock
format and battery label that shaffy-desktop's toggles own in 99-runtime.toml.
"""

from pathlib import Path
import tomllib

# What `shaffy-desktop clock` and `battery-label` write to 99-runtime.toml, on purpose
# over the managed defaults; tests/test_desktop_controls.py holds the helper to it.
TOGGLED = {'widget.clock.format', 'widget.battery.show_label'}


def _flatten(data, prefix=''):
    flat = {}
    for key, value in data.items():
        path = f'{prefix}{key}'
        if isinstance(value, dict):
            flat.update(_flatten(value, path + '.'))
        else:
            flat[path] = value
    return flat


def _read(path):
    try:
        return _flatten(tomllib.loads(Path(path).read_text()))
    except OSError, tomllib.TOMLDecodeError:
        return {}


def overlaps(rendered_dir, home):
    """Return (key, fragment value, runtime file, runtime value) for every overlap."""
    rendered_dir, home = Path(rendered_dir), Path(home)
    managed = {}
    for fragment in sorted(rendered_dir.glob('9[0-7]-*.toml')):
        managed.update(_read(fragment))
    runtime = [
        home / '.config/noctalia/99-runtime.toml',
        home / '.config/noctalia/config.toml',
        home / '.local/state/noctalia/settings.toml',
    ]
    found = []
    for source in runtime:
        for key, value in _read(source).items():
            if key in managed and not (source.name == '99-runtime.toml' and key in TOGGLED):
                found.append((key, managed[key], str(source), value))
    return found
