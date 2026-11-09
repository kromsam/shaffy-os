"""Package groups and a supplement's package choices.

defaults/packages/<group>.yml holds a role's repository `packages`, optional
`aur` entries and `required` subset; retired.yml the public retirements. A
supplement's `packages.extra`, and its profiles', installs more, `packages.omit`
leaves group packages and profiles' extras out without uninstalling them, and
`packages.remove` removes more. Removal happens only because a package is listed,
never by inference.
"""

from pathlib import Path
import re

NAME = re.compile(r'^[a-z0-9@_+][a-z0-9@._+-]*$')
# What Shaffy OS itself runs on: the bootstrap packages (lib/shaffy/bootstrap.py), the base system
# and the desktop. Removing one would break the next apply, so no list may remove it.
PROTECTED = frozenset(
    {
        'mise',
        'uv',
        'chezmoi',
        'python-yaml',
        'python-jsonschema',
        'snapper',
        'git',
        'base-devel',
        'bubblewrap',
        'base',
        'linux',
        'systemd',
        'pacman',
        'sudo',
        'python',
        'niri',
        'noctalia',
        'linux-cachyos',
        'linux-lts',
        'sddm',
        'limine',
        'grub',
        'mkinitcpio',
        'cryptsetup',
        'btrfs-progs',
        'xdg-user-dirs',
    }
)


def _names(value, label):
    if not isinstance(value, list) or not all(isinstance(v, str) and NAME.match(v) for v in value):
        return [f'{label} must be a list of package names']
    if len(set(value)) != len(value):
        return [f'{label} lists a package twice']
    return []


def load_groups(checkout, parse):
    """Return ({group: {'packages', 'aur', 'required'}}, retired, problems)."""
    directory = Path(checkout) / 'defaults/packages'
    groups, owner, problems = {}, {}, []
    for path in sorted(directory.glob('*.yml')):
        if path.stem == 'retired':
            continue
        data = parse(path)
        label = f'defaults/packages/{path.name}'
        if not isinstance(data, dict) or not {'packages', 'required'} <= set(data) <= {'packages', 'required', 'aur'}:
            problems.append(f'{label} must have `packages` and `required`, and optionally `aur`')
            continue
        invalid = _names(data['packages'], f'{label}: packages') + _names(data['required'], f'{label}: required')
        aur = data.get('aur', [])
        if not isinstance(aur, list) or not all(
            isinstance(entry, dict)
            and set(entry) == {'name'}
            and isinstance(entry['name'], str)
            and NAME.fullmatch(entry['name'])
            for entry in aur
        ):
            invalid.append(f'{label}: aur must be a list of {{name}} package entries')
        if invalid:
            problems += invalid
            continue
        names = data['packages'] + [entry['name'] for entry in aur]
        problems += [
            f'{label}: required {name} is not in its packages' for name in data['required'] if name not in names
        ]
        for name in names:
            if name in owner:
                problems.append(f'{label}: {name} already belongs to the {owner[name]} group')
            owner[name] = path.stem
        groups[path.stem] = {'packages': list(data['packages']), 'aur': aur, 'required': list(data['required'])}
    # A checkout without groups or retirements (a partial test copy) has none.
    retired_data = parse(directory / 'retired.yml') if (directory / 'retired.yml').exists() else {'retired': []}
    retired = retired_data.get('retired') if isinstance(retired_data, dict) else None
    invalid = _names(retired, 'defaults/packages/retired.yml: retired')
    problems += invalid
    retired = [] if invalid else retired
    problems += [
        f'defaults/packages/retired.yml: {name} is retired but belongs to the {owner[name]} group'
        for name in retired
        if name in owner
    ]
    problems += [
        f'defaults/packages/retired.yml: {name} is needed by Shaffy OS itself' for name in retired if name in PROTECTED
    ]
    return groups, retired, problems


def resolve(groups, retired, settings, origin=None):
    """The effective package state, problems for choices that contradict the groups, and notes.

    ORIGIN maps ('packages', 'extra', NAME) to the settings layer that added NAME (loader.fold).
    A profile's extra that a group installs is skipped with a note for its author, never refused,
    so a release that adopts a package cannot break every user of the profile; the supplement's
    is refused, as before. `omit` may also name a profile's extra, which leaves it out without
    uninstalling it, and `remove` of a package a profile's extra installs names that profile.
    """
    chosen = settings.get('packages', {})
    extra, omit, remove = (chosen.get(key, []) for key in ('extra', 'omit', 'remove'))
    owner = {
        name: group
        for group, data in groups.items()
        for name in data['packages'] + [entry['name'] for entry in data['aur']]
    }
    required = {name: group for group, data in groups.items() for name in data['required']}
    layers = {name: (origin or {}).get(('packages', 'extra', name)) for name in extra}
    profile = {name: layer for name, layer in layers.items() if layer is not None and layer.kind == 'profile'}
    problems, notes = [], []
    for name in omit:
        if name in required:
            problems.append(f'packages.omit: {name} is required by the {required[name]} group')
        elif name not in owner and name not in profile:
            problems.append(f'packages.omit: {name} is in no package group, so there is nothing to omit')
    for name in extra:
        if name in owner and name in profile:
            notes.append(
                f'packages.extra: {name}, which {profile[name].name} adds, is in the {owner[name]} group now, so '
                f'the profile need not list it; tell its author: {profile[name].issues}'
            )
        elif name in owner:
            problems.append(f'packages.extra: {name} is already in the {owner[name]} group')
    kept = [name for name in extra if name not in owner and name not in omit]
    installed = {name for name in owner if name not in omit} | set(kept)
    aur = {group: [entry for entry in data['aur'] if entry['name'] not in omit] for group, data in groups.items()}
    for name in remove:
        if name in PROTECTED:
            problems.append(f'packages.remove: {name} is needed by Shaffy OS itself')
        elif name in installed:
            if name in owner:
                by = f'the {owner[name]} group; omit it too'
            elif name in profile:
                by = f'packages.extra of profile {profile[name].name}; omit it too'
            else:
                by = 'packages.extra'
            problems.append(f'packages.remove: {name} is also installed by {by}')
    # An extra package the settings ask for outranks a public retirement.
    removed = sorted((set(retired) - set(kept)) | set(remove))
    return (
        {
            'groups': {
                group: [name for name in data['packages'] if name not in omit] for group, data in groups.items()
            },
            'aur': aur,
            'extra': kept,
            'remove': removed,
        },
        problems,
        notes,
    )
