"""The optional integrations and a supplement's lifecycle states for them.

defaults/integrations.yml is the public catalogue: each integration's scope, the
chezmoi targets that render only while it is managed, and the activation records
that switching it on creates. A supplement requests an integration with
`integrations: {name: managed|disabled|released}`; one it does not name stays
unmanaged.
"""

from pathlib import Path
import re

NAME = re.compile(r'^[a-z][a-z0-9_]*$')
KINDS = {
    'wants-link': {'unit', 'link'},
    'unit': {'unit', 'scope'},
    'autostart': {'path'},
    'config-key': {'file', 'keys'},
    'command': {'command', 'inverse'},
}


def load_catalogue(checkout, parse):
    """Return ({name: entry}, problems) from defaults/integrations.yml."""
    path = Path(checkout) / 'defaults/integrations.yml'
    if not path.exists():
        return {}, []
    data, problems = parse(path), []
    for name, entry in data.items():
        label = f'defaults/integrations.yml: {name}'
        if not NAME.match(name):
            problems.append(f'{label}: an integration name is lowercase letters, digits and _')
            continue
        if not isinstance(entry, dict) or entry.get('scope') not in ('home', 'system'):
            problems.append(f'{label}: needs a scope of home or system')
            continue
        if not isinstance(entry.get('targets'), list) or not all(isinstance(t, str) for t in entry['targets']):
            problems.append(f'{label}: targets must be a list of chezmoi target patterns')
        for record in entry.get('activation', []):
            kind = record.get('kind') if isinstance(record, dict) else None
            if kind not in KINDS or set(record) - {'kind'} != KINDS[kind]:
                problems.append(
                    f'{label}: an activation record needs kind and exactly {sorted(KINDS.get(kind, ()))} for its kind'
                )
            elif entry['scope'] == 'home' and not all(
                str(record[key]).startswith('~/') for key in ('link', 'path', 'file') if key in record
            ):
                problems.append(f'{label}: a home activation path starts with ~/')
        for key in ('adopt_requires', 'activate_requires'):
            required = entry.get(key)
            if required is not None and (
                not isinstance(required, dict)
                or set(required) not in ({'file', 'line', 'hint'}, {'file', 'json', 'hint'})
                or not str(required.get('file')).startswith('~/')
                or ('json' in required and not isinstance(required['json'], dict))
            ):
                problems.append(f'{label}: {key} needs file (under ~/), hint, and a line or a json mapping')
        if 'requires_setting' in entry and not isinstance(entry['requires_setting'], str):
            problems.append(f'{label}: requires_setting names one top-level setting')
    return data, problems


def _setting(settings, reference):
    """The value a `{settings.a.b}` reference names, or None when it is absent."""
    value = settings
    for part in reference[len('{settings.') : -1].split('.'):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _requirement(required, settings):
    """A precondition with its `{settings.…}` references replaced by their values."""
    if not required or 'json' not in required:
        return required
    return {
        **required,
        'json': {
            field: _setting(settings, value) if isinstance(value, str) and value.startswith('{settings.') else value
            for field, value in required['json'].items()
        },
    }


def resolve(catalogue, settings, required=None):
    """{name: {state, scope, targets, activation}} for every catalogued integration, or problems.

    REQUIRED, {integration: [profile ids]}, is what the listed profiles require: each must be in
    the catalogue and managed in the settings, or a problem names the profile and the line to add.
    A profile never sets an integration's state; the supplement decides.
    """
    requested = settings.get('integrations', {})
    problems = [
        f'integrations.{name}: there is no such integration in defaults/integrations.yml'
        for name in requested
        if name not in catalogue
    ]
    for name, needed_by in (required or {}).items():
        who = ' and '.join(needed_by) + (' require' if len(needed_by) > 1 else ' requires')
        if name not in catalogue:
            problems.append(f'integrations.{name}: there is no such integration in defaults/integrations.yml; {who} it')
        elif requested.get(name) != 'managed':
            problems.append(
                f'integrations.{name}: {who} it managed; set integrations.{name}: managed '
                '(docs/SUPPLEMENT.md#integrations)'
            )
    problems += [
        f'integrations.{name}: managing it needs the {catalogue[name]["requires_setting"]} settings'
        for name, state in requested.items()
        if name in catalogue
        and state == 'managed'
        and catalogue[name].get('requires_setting')
        and catalogue[name]['requires_setting'] not in settings
    ]
    return {
        name: {
            'state': requested.get(name),
            'scope': entry['scope'],
            'targets': list(entry['targets']),
            'activation': list(entry.get('activation', [])),
            'adopt_requires': _requirement(entry.get('adopt_requires'), settings),
            'activate_requires': _requirement(entry.get('activate_requires'), settings),
        }
        for name, entry in catalogue.items()
    }, problems
