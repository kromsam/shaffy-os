"""Full settings loader: parse, validate and merge defaults with a supplement.

PyYAML parses (YAML 1.2 core typing, duplicate keys rejected), python-jsonschema
validates the structure, and hand-written checks cover what a schema cannot.
"""

import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import quote

import jsonschema
import yaml

from . import profiles
from .integrations import load_catalogue
from .integrations import resolve as resolve_integrations
from .packages import load_groups
from .packages import resolve as resolve_packages
from .selection import resolve
from .source import release, release_key

SUPPORTED_VERSIONS = (1,)
SYSTEM_OVERRIDES = ('keyboard_hwdb',)
SYSTEM_OVERRIDE_LIMIT = 64 * 1024
IDENTITY_KEYS = ('schema_version', 'supplement_id', 'minimum_release')
# Lists that gather every layer's items, in layer order, instead of replacing.
UNION = (('packages', 'extra'), ('lazyvim', 'extras'))
# Maps whose entries are units; an entry merges key by key over the defaults' one.
KEYED = (('ai', 'mcp_servers'), ('ai', 'claude_plugins'))
# Resolved per layer against its own root, never folded.
FILE_SECTIONS = ('overrides', 'assets')


@dataclass(frozen=True)
class Layer:
    """One source of settings in merge order: the defaults, a profile, or the supplement."""

    kind: str  # 'defaults', 'profile' or 'supplement'
    name: str  # 'defaults', 'kromsam/writing' or 'supplement'
    label: str  # the file messages name: its config.yaml or profile.yaml
    root: object  # where its references resolve; None for the defaults
    prefix: str  # root relative to the supplement: '' or 'profiles/kromsam/writing/'
    data: dict  # its settings: identity and profile keys removed, overrides and assets included
    requires: tuple = ()
    issues: str = ''  # where to tell a profile's author (profiles.issues); '' for the others


class SettingsError(Exception):
    """One or more problems, each a line naming the file and the key."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__('\n'.join(self.problems))


class _Loader(yaml.SafeLoader):
    """YAML 1.2 core schema: only true/false are booleans; no timestamps,
    sexagesimal numbers or 0-prefixed octals. Editors read files the same way."""


_Loader.yaml_implicit_resolvers = {}
for _tag, _pattern, _first in (
    ('bool', r'^(?:true|True|TRUE|false|False|FALSE)$', 'tTfF'),
    ('null', r'^(?:~|null|Null|NULL|)$', ['~', 'n', 'N', '']),
    ('int', r'^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$', '-+0123456789'),
    (
        'float',
        r'^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?'
        r'|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$',
        '-+.0123456789',
    ),
):
    _Loader.add_implicit_resolver(f'tag:yaml.org,2002:{_tag}', re.compile(_pattern), list(_first))


def _construct_int(loader, node):
    value = loader.construct_scalar(node)
    if value.startswith('0o'):
        return int(value[2:], 8)
    if value.startswith('0x'):
        return int(value[2:], 16)
    return int(value, 10)  # "0600" is decimal 600 in YAML 1.2, never octal


def _construct_mapping(loader, node):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise yaml.constructor.ConstructorError(None, None, f'duplicate key {key!r}', key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_Loader.add_constructor('tag:yaml.org,2002:int', _construct_int)
_Loader.add_constructor('tag:yaml.org,2002:map', _construct_mapping)


class _NoAliases(_Loader):
    """_Loader for a file someone else wrote: an alias is refused, since a few nested ones
    multiply into more data than a printout of the settings can hold."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.events.AliasEvent):
            event = self.peek_event()
            raise yaml.composer.ComposerError(
                None, None, f'an alias (*{event.anchor}); write the value out in full', event.start_mark
            )
        return super().compose_node(parent, index)


def parse(path, aliases=True):
    """Parse one YAML file into plain data, or raise SettingsError, which names an alias as well
    unless ALIASES."""
    try:
        data = yaml.load(Path(path).read_text(), Loader=_Loader if aliases else _NoAliases)
    except OSError as error:
        raise SettingsError([f'{path}: cannot read: {error.strerror}']) from error
    except yaml.YAMLError as error:
        mark = getattr(error, 'problem_mark', None)
        where = f':{mark.line + 1}:{mark.column + 1}' if mark else ''
        problem = getattr(error, 'problem', None) or str(error)
        raise SettingsError([f'{path}{where}: {problem}']) from error
    if not isinstance(data, dict):
        raise SettingsError([f'{path}: the top level must be a mapping of settings'])
    return data


def _describe(error):
    path = '.'.join(str(part) for part in error.absolute_path) or '(top level)'
    kind = error.validator
    if kind == 'additionalProperties':
        extra = sorted(set(error.instance) - set(error.schema.get('properties', {})))
        return path, 'unknown key' + ('s ' if len(extra) > 1 else ' ') + ', '.join(map(repr, extra))
    if kind == 'type':
        found = 'null' if error.instance is None else type(error.instance).__name__
        return path, f'must be of type {error.validator_value}, found {found} {error.instance!r}'
    if kind == 'minProperties':
        return path, 'must not be empty'
    if kind == 'propertyNames':
        return path, f'invalid name {error.instance!r}'
    if kind == 'pattern':
        return path, f'{error.instance!r} does not match {error.validator_value}'
    return path, error.message


def validate(data, schema, label):
    """Return problems: unsupported version first, then schema errors in key order."""
    version = data.get('schema_version')
    if 'schema_version' in data and version not in SUPPORTED_VERSIONS:
        return [
            f'{label}: schema_version: {version!r} is not supported; this Shaffy OS reads '
            + ', '.join(map(str, SUPPORTED_VERSIONS))
        ]
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])
    return [f'{label}: ' + ': '.join(_describe(error)) for error in errors]


def release_problem(checkout, wanted, needs='this supplement'):
    """Why this checkout is older than the minimum_release NEEDS asks for, or None."""
    if not isinstance(wanted, str) or not release_key(wanted):
        return None  # absent, or malformed, which the schema reports
    have = release(checkout)
    if have is None:
        return (
            f'minimum_release: {needs} needs Shaffy OS {wanted} or later, and no release '
            "tag is reachable from this checkout's HEAD; fetch the tags (git fetch --tags)"
        )
    if release_key(have) < release_key(wanted):
        return (
            f'minimum_release: {needs} needs Shaffy OS {wanted} or later; this checkout '
            f'is at {have}; update it to a newer release'
        )
    return None


def defaults_schema(schema):
    """Defaults carry schema_version but no supplement_id, minimum_release, dotfiles or profiles."""
    result = copy.deepcopy(schema)
    result['required'] = ['schema_version']
    result['properties'].pop('supplement_id')
    result['properties'].pop('minimum_release')
    # The dotfiles are a folder in one person's supplement, never a public default, and the
    # public desktop vendors no profile: a supplement chooses them.
    result['properties'].pop('dotfiles')
    result['properties'].pop('profiles')
    return result


def merge(base, override):
    """Mappings merge recursively; scalars and lists replace; false and [] are kept."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _units(data, path=()):
    """(path, value) for every unit DATA sets: a leaf, a UNION list or a KEYED entry."""
    for key, value in data.items():
        here = (*path, key)
        if here in UNION or path in KEYED or not isinstance(value, dict):
            yield here, value
        else:
            yield from _units(value, here)


def _get(data, path, default=None):
    for part in path:
        if not isinstance(data, dict) or part not in data:
            return default
        data = data[part]
    return data


def _put(data, path, value):
    for part in path[:-1]:
        if not isinstance(data.get(part), dict):
            data[part] = {}
        data = data[part]
    data[path[-1]] = value


def dotted(path):
    return '.'.join(map(str, path))


def fold(layers):
    """Merge the layers' settings in order (docs/PROFILES.md, "How layers combine").

    Returns (settings, origin, problems). ORIGIN maps each unit's path to the Layer that set it
    last, each field of a KEYED entry (path + (field,)) to the layer whose value it holds, and
    each UNION item's path (path + (item,)) to the layer that added it first, or to the
    supplement when config.yaml lists it as well. overrides and assets are not folded:
    _layer_files resolves them per layer and _assets joins the asset entries. Two profiles that
    give one unit different values are a problem naming both, unless the supplement sets that unit
    as well; then the supplement's value, over the defaults' if there is one, replaces both
    profiles'. With the defaults and a supplement alone the result equals merge(defaults,
    supplement), which a test holds.
    """
    settings, origin, held, contested, base, fields = {}, {}, {}, {}, {}, {}
    for layer in layers:
        for path, value in _units({k: v for k, v in layer.data.items() if k not in FILE_SECTIONS}):
            if path in UNION:
                items = list(_get(settings, path, []))
                for item in value:
                    if item not in items:
                        items.append(item)
                        origin[(*path, item)] = layer
                    elif layer.kind == 'supplement':
                        origin[(*path, item)] = layer  # config.yaml asks for it too: it stays without the profile
                _put(settings, path, items)
                continue
            before = _get(settings, path)
            if layer.kind == 'defaults':
                base[path] = (layer, value)
            elif layer.kind == 'profile':
                first = held.setdefault(path, (layer, value))
                if first[1] != value:
                    contested.setdefault(path, [first[0]]).append(layer)
            elif contested.pop(path, None):
                owner, before = base.get(path, (None, None))  # config.yaml settles it: neither profile's entry stays
                fields[path] = dict.fromkeys(before, owner) if isinstance(before, dict) else {}
            if path[:-1] in KEYED and isinstance(value, dict):
                own = value
                if isinstance(before, dict):
                    value = merge(before, value)  # an MCP server, field by field over the one before it
                else:
                    fields[path] = {}
                fields.setdefault(path, {}).update(dict.fromkeys(own, layer))
            _put(settings, path, copy.deepcopy(value))
            origin[path] = layer
    origin.update({(*path, name): owner for path, named in fields.items() for name, owner in named.items()})
    return settings, origin, [_contested(dotted(path), setters) for path, setters in contested.items()]


def _contested(what, setters):
    """The problem of one entry that two or more profiles, SETTERS, give different values."""
    return (
        f'{what}: {" and ".join(layer.name for layer in setters)} set it differently. Write the entry '
        'you want in config.yaml; it replaces both. If a profile should not set it, tell its author: '
        + ', '.join(dict.fromkeys(layer.issues for layer in setters))
    )


def check_reference(root, reference, label, folder=False, within='the supplement'):
    """A referenced file, or FOLDER, must exist inside ROOT, WITHIN in messages, after resolving symlinks."""
    if os.path.isabs(reference):
        raise SettingsError([f'{label}: {reference!r} must be relative to {within}'])
    root = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root, reference))
    if os.path.commonpath([root, target]) != root:
        raise SettingsError([f'{label}: {reference!r} resolves outside {within}'])
    if folder and target == root:
        raise SettingsError([f'{label}: {reference!r} is {within} itself; name a folder inside it'])
    if not (os.path.isdir if folder else os.path.isfile)(target):
        kind = ' as a folder' if folder else ''
        raise SettingsError([f'{label}: {reference!r} does not exist in {within}{kind}'])
    return target


def _into_profiles(root, reference, target):
    """Whether REFERENCE, written in config.yaml, or the TARGET it resolves to lies in profiles/,
    the folder ./bin/shaffy profile writes."""
    written = os.path.normpath(reference)
    folder = os.path.realpath(os.path.join(root, profiles.FOLDER))
    return (
        written == profiles.FOLDER
        or written.startswith(profiles.FOLDER + '/')
        or os.path.commonpath([folder, target]) == folder
    )


# restic-home writes its verification marker here, so a backup must include it.
BACKUP_MARKER = ('.local', 'state', 'shaffy', 'home-backup')


def _named(layer):
    """How a message names the layer that set a value."""
    if layer is None or layer.kind == 'supplement':
        return 'config.yaml'
    return layer.name if layer.kind == 'profile' else 'the public defaults'


def _effective_problems(settings, origin=None):
    """Problems of the folded result; one about a profile's entry carries that profile's file, and
    a marketplace written with two repositories names the layer of each plugin."""
    origin = origin or {}

    def where(path):
        layer = origin.get(path)
        return f'{layer.label}: ' if layer is not None and layer.kind == 'profile' else 'effective settings: '

    servers = settings.get('ai', {}).get('mcp_servers', {})
    problems = []
    battery = settings.get('power', {}).get('low_battery_hibernate')
    if battery and not battery['low_percent'] > battery['critical_percent'] > battery['action_percent']:
        problems.append('power.low_battery_hibernate: require low_percent > critical_percent > action_percent')
    limit = settings.get('power', {}).get('charge_limit', {})
    if 'start' in limit and not limit['start'] < limit['end']:
        problems.append('power.charge_limit: require start < end')
    for name, spec in servers.items():
        if not spec.get('enabled', True):
            continue
        label = where(('ai', 'mcp_servers', name)) + f'ai.mcp_servers.{name}'
        if 'command' not in spec and 'url' not in spec:
            problems.append(f'{label}: needs a command or a url')
        elif 'command' in spec and 'url' in spec:
            command, url = (origin.get(('ai', 'mcp_servers', name, field)) for field in ('command', 'url'))
            both = f' (from {_named(command)}) and a url (from {_named(url)})' if command != url else ' and a url'
            problems.append(f'{label}: has both a command{both}')
        elif 'url' in spec and ('args' in spec or 'env' in spec):
            problems.append(f'{label}: args and env need a command')
    # Claude Code knows a marketplace by its name alone, and merge-ai-config.py writes one repository
    # per name, so a second repository would silently serve the first one's plugins.
    marketplaces = {}
    for plugin, repository in sorted(settings.get('ai', {}).get('claude_plugins', {}).items()):
        marketplaces.setdefault(plugin.partition('@')[2], {}).setdefault(repository, []).append(plugin)
    for marketplace, repositories in marketplaces.items():
        if len(repositories) < 2:
            continue
        setters = {
            plugin: origin.get(('ai', 'claude_plugins', plugin)) for group in repositories.values() for plugin in group
        }
        named = ' and '.join(
            f'{repository} (' + ', '.join(f'{plugin} from {_named(setters[plugin])}' for plugin in plugins) + ')'
            for repository, plugins in sorted(repositories.items())
        )
        authors = dict.fromkeys(layer.issues for layer in setters.values() if layer and layer.kind == 'profile')
        problems.append(
            f'ai.claude_plugins: the marketplace {marketplace} is named with {named}; one marketplace is one '
            "repository. Write these plugins in config.yaml with the one you want; it replaces a profile's entry."
            + (f' If a profile names the wrong one, tell its author: {", ".join(authors)}' if authors else '')
        )
    for exclude in settings.get('backup', {}).get('excludes', []):
        parts = tuple(part for part in exclude[2:].split('/') if part)
        if parts == BACKUP_MARKER[: len(parts)]:
            problems.append(
                f'backup.excludes: {exclude} would leave out ~/{"/".join(BACKUP_MARKER)}, '
                "where each backup's verification marker is written"
            )
    return problems


def _strip(data, keys):
    return {key: value for key, value in data.items() if key not in keys}


def _asset(layer, target, path, reference, label, executable=False, private=False, fragment=None):
    """(entry, problem) for one file LAYER installs at TARGET: whole, with its layer named."""
    try:
        with open(path, 'rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    except OSError as error:
        return None, f'{label}: {reference!r} cannot be read: {error.strerror}'
    entry = {
        'target': target,
        'path': path,
        'url': 'file://' + quote(path),
        'sha256': digest,
        'executable': executable,
        'private': private,
        'layer': layer.name,
    }
    if fragment:
        entry['fragment'] = fragment
    return entry, None


def _layer_files(layer, position):
    """A layer's overrides and assets, resolved against its own root; returns
    (fragments, assets, hwdb, written, problems).

    The supplement's home fragments stay {name: path}; a profile's become asset entries at
    profiles.place(tool, position, layer.name) with `fragment: tool`. keyboard_hwdb comes back
    as text for _hwdb, and a profile's text with a line profiles.hwdb_unit_matches finds is a
    problem naming the file and line. Every asset entry is whole and carries its layer. WRITTEN
    holds the paths relative to the supplement (layer.prefix + reference). A supplement reference
    into profiles/ is refused.
    """
    profile = layer.kind == 'profile'
    within = 'the profile' if profile else 'the supplement'
    prefix = f'{layer.label}: ' if profile else ''
    fragments, assets, hwdb, written, problems = {}, [], None, set(), []

    def locate(reference, key):
        path = check_reference(layer.root, reference, prefix + key, within=within)
        if not profile and _into_profiles(layer.root, reference, path):
            raise SettingsError(
                [
                    f'{key}: {reference!r} leads into {profiles.FOLDER}/, which ./bin/shaffy profile writes; '
                    'list the profile under profiles: instead'
                ]
            )
        written.add(layer.prefix + os.path.normpath(reference))
        return path

    for name, reference in layer.data.get('overrides', {}).items():
        key = f'overrides.{name}'
        try:
            path = locate(reference, key)
        except SettingsError as error:
            problems += error.problems
            continue
        if name not in SYSTEM_OVERRIDES:
            if not profile:
                fragments[name] = path
                continue
            target = profiles.place(name, position, layer.name)
            entry, problem = _asset(layer, target, path, reference, prefix + key, fragment=name)
            if entry:
                assets.append(entry)
            else:
                problems.append(problem)
            continue
        # System-scope fragments travel inside the loaded settings, so root reads them from its
        # verified copy of the operation's settings rather than from the user's files.
        if not profile:
            fragments[name] = path
        try:
            with open(path, encoding='utf-8') as stream:
                text = stream.read(SYSTEM_OVERRIDE_LIMIT + 1)
        except (OSError, UnicodeDecodeError) as error:
            problems.append(f'{prefix}{key}: cannot be read as text: {error}')
            continue
        if len(text) > SYSTEM_OVERRIDE_LIMIT:
            problems.append(f'{prefix}{key}: larger than {SYSTEM_OVERRIDE_LIMIT} characters')
            continue
        units = profiles.hwdb_unit_matches(text) if profile else []
        if units:
            tell = f' Tell the author: {layer.issues}' if layer.issues else ''
            problems += [
                f'{path}:{line}: this match names one unit (a port position, a unique id or a serial). A profile '
                f'shares a map for a keyboard model: match its vendor and product (evdev:input:b0003vXXXXpYYYY*).{tell}'
                for line in units
            ]
            continue
        hwdb = text
    for target, value in sorted(layer.data.get('assets', {}).items()):
        key = f'assets.{target}'
        place = profiles.place_of(target[2:])
        if place:
            problems.append(
                f'{prefix}{key}: ~/{place} is where profile fragments go, and an apply deletes a file there that no '
                'profile sets, so an asset there would stop every apply after the first; choose another target'
            )
            continue
        reference = value['file'] if isinstance(value, dict) else value
        try:
            path = locate(reference, key)
        except SettingsError as error:
            problems += error.problems
            continue
        entry, problem = _asset(
            layer,
            target[2:],
            path,
            reference,
            prefix + key,
            executable=bool(isinstance(value, dict) and value.get('executable')),
            private=bool(isinstance(value, dict) and value.get('private')),
        )
        if entry:
            assets.append(entry)
        else:
            problems.append(problem)
    return fragments, assets, hwdb, written, problems


def _hwdb(parts):
    """(text, problems) from [(layer, text)] in layer order. The supplement's map alone is installed
    as it is, as today. With a profile's map, each part is headed by a comment naming its layer and
    the parts are separated by a blank line, so no record runs into the next and udev's last entry
    wins for the supplement. (None, []) without parts; a text past SYSTEM_OVERRIDE_LIMIT names the
    largest parts."""
    if not parts:
        return None, []
    if len(parts) == 1 and parts[0][0].kind == 'supplement':
        return parts[0][1], []
    text = '\n'.join(
        '# Shaffy OS: from '
        + (f'profile {layer.name}' if layer.kind == 'profile' else 'the supplement')
        + '\n'
        + part.rstrip('\n')
        + '\n'
        for layer, part in parts
    )
    if len(text) > SYSTEM_OVERRIDE_LIMIT:
        largest = sorted(parts, key=lambda item: len(item[1]), reverse=True)[:2]
        return None, [
            f'overrides.keyboard_hwdb: the maps together are larger than {SYSTEM_OVERRIDE_LIMIT} characters; '
            'the largest: ' + ', '.join(f'{layer.name} ({len(part)} characters)' for layer, part in largest)
        ]
    return text, []


def _assets(entries):
    """(assets, problems, notes) from [(layer, entry)]: one entry per target. The supplement's
    replaces a profile's, with a note; two profiles' entries that differ are a problem naming
    both, unless the supplement has the target; identical ones are kept once."""
    by_target = {}
    for layer, entry in entries:
        by_target.setdefault(entry['target'], []).append((layer, entry))
    assets, problems, notes = [], [], []
    for target, candidates in sorted(by_target.items()):
        own = [entry for layer, entry in candidates if layer.kind == 'supplement']
        theirs = [(layer, entry) for layer, entry in candidates if layer.kind == 'profile']
        if own:
            assets.append(own[0])
            notes += [f"assets.~/{target}: config.yaml replaces {layer.name}'s file" for layer, _ in theirs]
        elif len({(entry['sha256'], entry['executable'], entry['private']) for _, entry in theirs}) > 1:
            problems.append(_contested(f'assets.~/{target}', [layer for layer, _ in theirs]))
        else:
            assets.append(theirs[0][1])
    return assets, problems, notes


def _profile_layer(checkout, schema, profile):
    """(Layer, summary, problems) for one vendored profile, read as the format asks."""
    locked = profile.locked
    label = str(profile.folder / profiles.MANIFEST)
    try:
        data, problems = profiles.read_profile(
            profile.folder,
            schema,
            label,
            parse=parse,
            validate=validate,
            release_problem=lambda wanted: release_problem(checkout, wanted, f'profile {locked.id}'),
            profile_id=locked.id,
        )
    except SettingsError as error:
        return None, None, error.problems
    requires = data.get('requires') if isinstance(data.get('requires'), list) else []
    layer = Layer(
        'profile',
        locked.id,
        label,
        profile.folder,
        profile.prefix,
        _strip(data, profiles.PROFILE_KEYS),
        tuple(requires),
        profiles.issues(locked.source),
    )
    summary = {
        'id': locked.id,
        'description': data.get('description'),
        'source': locked.source,
        'path': locked.path,
        'ref': locked.ref,
        'commit': locked.commit,
        'requires': list(requires),
    }
    return layer, summary, problems


def check_profile(checkout, folder, label=None, profile_id=None, issues=''):
    """Problems of one profile folder, as a load finds them, without the fold:
    profiles.read_profile with this module's parse, validate and release check, then _layer_files
    on it (every reference inside the folder after links, the keyboard map's text, no match that
    names one unit). Vendoring runs it on the staged copy and profile check on an author's working
    tree, so a profile that vendors also loads."""
    checkout, folder = Path(checkout), Path(folder)
    label = label or str(folder / profiles.MANIFEST)
    schema = json.loads((checkout / 'schema/supplement.schema.json').read_text())
    needs = f'profile {profile_id}' if profile_id else 'this profile'
    try:
        data, problems = profiles.read_profile(
            folder,
            schema,
            label,
            parse=parse,
            validate=validate,
            release_problem=lambda wanted: release_problem(checkout, wanted, needs),
            profile_id=profile_id,
        )
    except SettingsError as error:
        return error.problems
    if problems:
        return problems
    layer = Layer(
        'profile', profile_id or 'profile', label, folder, '', _strip(data, profiles.PROFILE_KEYS), (), issues
    )
    return _layer_files(layer, 1)[4]


def load(checkout, supplement=None, without=False, home=None, listed=None):
    """Resolve, parse, validate and fold the layers. Raises SelectionError or SettingsError.

    LISTED, when given, replaces config.yaml's profiles list: a profile command's review loads the
    settings as they will be once the person lists what it vendored.
    """
    checkout = Path(checkout)
    selection = resolve(supplement, without, home)
    schema = json.loads((checkout / 'schema/supplement.schema.json').read_text())
    defaults_path = checkout / 'defaults/config.yaml'
    defaults = parse(defaults_path)
    problems = validate(defaults, defaults_schema(schema), str(defaults_path))
    layers = [Layer('defaults', 'defaults', str(defaults_path), None, '', _strip(defaults, IDENTITY_KEYS))]
    supplement_id, root, found, summaries = None, None, [], []
    if selection['mode'] == 'supplement':
        root = Path(selection['path'])
        config = root / 'config.yaml'
        overrides = parse(config)
        # Too old a checkout would also report the newer fields as unknown keys; say only that.
        too_old = release_problem(checkout, overrides.get('minimum_release'))
        problems += [f'{config}: {too_old}'] if too_old else validate(overrides, schema, str(config))
        supplement_id = overrides.get('supplement_id')
        if not problems:
            # Each listed profile is one more layer, its bytes verified against the lock first.
            found, problems = profiles.vendored(root, overrides.get('profiles', []) if listed is None else listed)
            for profile in found:
                layer, summary, read_problems = _profile_layer(checkout, schema, profile)
                problems += read_problems
                if layer is not None:
                    layers.append(layer)
                    summaries.append(summary)
        layers.append(
            Layer('supplement', 'supplement', str(config), root, '', _strip(overrides, (*IDENTITY_KEYS, 'profiles')))
        )
    if problems:
        raise SettingsError(problems)  # later checks would only repeat what a bad layer says
    settings, origin, problems = fold(layers)
    problems += _effective_problems(settings, origin)
    fragments, entries, parts, written = {}, [], [], set()
    for position, layer in enumerate(layers):
        own, layer_assets, hwdb, layer_written, layer_problems = _layer_files(layer, position)
        fragments.update(own)
        entries += [(layer, entry) for entry in layer_assets]
        if hwdb is not None:
            parts.append((layer, hwdb))
        written |= layer_written
        problems += layer_problems
    assets, asset_problems, notes = _assets(entries)
    hwdb, hwdb_problems = _hwdb(parts)
    problems += asset_problems + hwdb_problems
    # A profile may only name files the lock verified.
    locked = set(profiles.files(found))
    for path in sorted(written - locked):
        if path.startswith(profiles.FOLDER + '/'):
            profile_id = '/'.join(path.split('/')[1:3])
            problems.append(
                f'{path}: not in {profiles.LOCK}; run ./bin/shaffy profile update {profile_id} or git restore '
                f'{profiles.FOLDER}/'
            )
    # Only the folder is checked here; the capture copies the files in it.
    dotfiles = settings.pop('dotfiles', None)
    if dotfiles is not None:
        try:
            target = check_reference(root, dotfiles, 'dotfiles', folder=True)
            if _into_profiles(root, dotfiles, target):
                raise SettingsError(
                    [f'dotfiles: {dotfiles!r} leads into {profiles.FOLDER}/, which ./bin/shaffy profile writes']
                )
            dotfiles = os.path.normpath(dotfiles)
        except SettingsError as error:
            problems += error.problems
    groups, retired, group_problems = load_groups(checkout, parse)
    packages, choice_problems, package_notes = resolve_packages(groups, retired, settings, origin)
    problems += group_problems or choice_problems
    notes += package_notes
    catalogue, catalogue_problems = load_catalogue(checkout, parse)
    required = {}
    for layer in layers:
        for name in layer.requires:
            required.setdefault(name, []).append(layer.name)
    integrations, request_problems = resolve_integrations(catalogue, settings, required)
    problems += catalogue_problems or request_problems
    if problems:
        raise SettingsError(problems)
    # `references` are resolved paths, for cache stamps; `reference_paths` are the paths as
    # config.yaml and the lock write them, which a capture must reproduce even through symlinks.
    references = {*fragments.values(), *(entry['path'] for _, entry in entries)}
    references |= {os.path.realpath(root / path) for path in locked}
    return {
        'shaffy': {
            'schema_version': 1,
            'selection': {**selection, 'supplement_id': supplement_id},
            'settings': settings,
            'overrides': fragments,
            'references': sorted(references),
            'reference_paths': sorted(written | locked),
            'assets': assets,
            'dotfiles': dotfiles,  # the folder as config.yaml names it, or None
            'system_overrides': {'keyboard_hwdb': hwdb} if hwdb is not None else {},
            'packages': packages,
            'integrations': integrations,
            'profiles': summaries,  # in merge order
            'provenance': {dotted(path): layer.name for path, layer in origin.items() if layer.kind == 'profile'},
            'fragment_places': profiles.places(),
            'notes': notes,
        }
    }
