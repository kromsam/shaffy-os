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


def parse(path):
    """Parse one YAML file into plain data, or raise SettingsError."""
    try:
        data = yaml.load(Path(path).read_text(), Loader=_Loader)
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


def release_problem(checkout, wanted):
    """Why this checkout is older than the supplement's minimum_release, or None."""
    if not isinstance(wanted, str) or not release_key(wanted):
        return None  # absent, or malformed, which the schema reports
    have = release(checkout)
    if have is None:
        return (
            f'minimum_release: this supplement needs Shaffy OS {wanted} or later, and no release '
            "tag is reachable from this checkout's HEAD; fetch the tags (git fetch --tags)"
        )
    if release_key(have) < release_key(wanted):
        return (
            f'minimum_release: this supplement needs Shaffy OS {wanted} or later; this checkout '
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
    last, and each UNION item's path (path + (item,)) to the layer that added it first. overrides
    and assets are not folded: _layer_files resolves them per layer and _assets joins the asset
    entries. Two profiles that give one unit different values are a problem naming both, unless
    the supplement sets that unit as well; then the supplement's value, over the defaults' if
    there is one, replaces both profiles'. With the defaults and a supplement alone the result
    equals merge(defaults, supplement), which a test holds.
    """
    settings, origin, held, contested, base = {}, {}, {}, {}, {}
    for layer in layers:
        for path, value in _units({k: v for k, v in layer.data.items() if k not in FILE_SECTIONS}):
            if path in UNION:
                items = list(_get(settings, path, []))
                for item in value:
                    if item not in items:
                        items.append(item)
                        origin[(*path, item)] = layer
                _put(settings, path, items)
                continue
            before = _get(settings, path)
            if layer.kind == 'defaults':
                base[path] = value
            elif layer.kind == 'profile':
                first = held.setdefault(path, (layer, value))
                if first[1] != value:
                    contested.setdefault(path, [first[0]]).append(layer)
            elif contested.pop(path, None):
                before = base.get(path)  # config.yaml settles it: neither profile's entry stays
            if path[:-1] in KEYED and isinstance(value, dict) and isinstance(before, dict):
                value = merge(before, value)  # an MCP server, field by field over the one before it
            _put(settings, path, copy.deepcopy(value))
            origin[path] = layer
    problems = [
        f'{dotted(path)}: {" and ".join(layer.name for layer in setters)} set it differently. Write the entry '
        'you want in config.yaml; it replaces both. If a profile should not set it, tell its author: '
        + ', '.join(dict.fromkeys(layer.issues for layer in setters))
        for path, setters in contested.items()
    ]
    return settings, origin, problems


def check_reference(root, reference, label, folder=False):
    """A referenced file, or FOLDER, must exist inside the supplement, after resolving symlinks."""
    if os.path.isabs(reference):
        raise SettingsError([f'{label}: {reference!r} must be relative to the supplement'])
    root = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root, reference))
    if os.path.commonpath([root, target]) != root:
        raise SettingsError([f'{label}: {reference!r} resolves outside the supplement'])
    if folder and target == root:
        raise SettingsError([f'{label}: {reference!r} is the supplement itself; name a folder inside it'])
    if not (os.path.isdir if folder else os.path.isfile)(target):
        kind = ' as a folder' if folder else ''
        raise SettingsError([f'{label}: {reference!r} does not exist in the supplement{kind}'])
    return target


# restic-home writes its verification marker here, so a backup must include it.
BACKUP_MARKER = ('.local', 'state', 'shaffy', 'home-backup')


def _effective_problems(settings):
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
        if 'command' not in spec and 'url' not in spec:
            problems.append(f'effective settings: ai.mcp_servers.{name}: needs a command or a url')
        elif 'command' in spec and 'url' in spec:
            problems.append(f'effective settings: ai.mcp_servers.{name}: has both a command and a url')
        elif 'url' in spec and ('args' in spec or 'env' in spec):
            problems.append(f'effective settings: ai.mcp_servers.{name}: args and env need a command')
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


def load(checkout, supplement=None, without=False, home=None):
    """Resolve, parse, validate and fold the layers. Raises SelectionError or SettingsError."""
    checkout = Path(checkout)
    selection = resolve(supplement, without, home)
    schema = json.loads((checkout / 'schema/supplement.schema.json').read_text())
    defaults_path = checkout / 'defaults/config.yaml'
    defaults = parse(defaults_path)
    problems = validate(defaults, defaults_schema(schema), str(defaults_path))
    layers = [Layer('defaults', 'defaults', str(defaults_path), None, '', _strip(defaults, IDENTITY_KEYS))]
    supplement_id = None
    if selection['mode'] == 'supplement':
        root = Path(selection['path'])
        config = root / 'config.yaml'
        overrides = parse(config)
        # Too old a checkout would also report the newer fields as unknown keys; say only that.
        too_old = release_problem(checkout, overrides.get('minimum_release'))
        problems += [f'{config}: {too_old}'] if too_old else validate(overrides, schema, str(config))
        supplement_id = overrides.get('supplement_id')
        layers.append(Layer('supplement', 'supplement', str(config), root, '', _strip(overrides, IDENTITY_KEYS)))
    if problems:
        raise SettingsError(problems)
    settings, _, problems = fold(layers)
    problems += _effective_problems(settings)
    own = layers[-1].data if layers[-1].kind == 'supplement' else {}
    fragments, written, system_overrides = {}, set(), {}
    for name, reference in own.get('overrides', {}).items():
        try:
            fragments[name] = check_reference(selection['path'], reference, f'overrides.{name}')
            written.add(os.path.normpath(reference))
        except SettingsError as error:
            problems += error.problems
    # System-scope fragments travel inside the loaded settings, so root reads them from its
    # verified copy of the operation's settings rather than from the user's files.
    for name in SYSTEM_OVERRIDES:
        if name in fragments:
            try:
                with open(fragments[name], encoding='utf-8') as stream:
                    text = stream.read(SYSTEM_OVERRIDE_LIMIT + 1)
            except (OSError, UnicodeDecodeError) as error:
                problems.append(f'overrides.{name}: cannot be read as text: {error}')
                continue
            if len(text) > SYSTEM_OVERRIDE_LIMIT:
                problems.append(f'overrides.{name}: larger than {SYSTEM_OVERRIDE_LIMIT} characters')
                continue
            system_overrides[name] = text
    assets = []
    for target, value in sorted(own.get('assets', {}).items()):
        reference = value['file'] if isinstance(value, dict) else value
        try:
            path = check_reference(selection['path'], reference, f'assets.{target}')
        except SettingsError as error:
            problems += error.problems
            continue
        try:
            with open(path, 'rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        except OSError as error:
            problems.append(f'assets.{target}: {reference!r} cannot be read: {error.strerror}')
            continue
        written.add(os.path.normpath(reference))
        assets.append(
            {
                'target': target[2:],
                'path': path,
                'url': 'file://' + quote(path),
                'sha256': digest,
                'executable': bool(isinstance(value, dict) and value.get('executable')),
                'private': bool(isinstance(value, dict) and value.get('private')),
            }
        )
    # Only the folder is checked here; the capture copies the files in it.
    dotfiles = settings.pop('dotfiles', None)
    if dotfiles is not None:
        try:
            check_reference(selection['path'], dotfiles, 'dotfiles', folder=True)
            dotfiles = os.path.normpath(dotfiles)
        except SettingsError as error:
            problems += error.problems
    groups, retired, group_problems = load_groups(checkout, parse)
    packages, choice_problems = resolve_packages(groups, retired, settings)
    problems += group_problems or choice_problems
    catalogue, catalogue_problems = load_catalogue(checkout, parse)
    integrations, request_problems = resolve_integrations(catalogue, settings)
    problems += catalogue_problems or request_problems
    if problems:
        raise SettingsError(problems)
    # `references` are resolved paths, for cache stamps; `reference_paths` are the paths as
    # config.yaml writes them, which a capture must reproduce even through symlinks.
    return {
        'shaffy': {
            'schema_version': 1,
            'selection': {**selection, 'supplement_id': supplement_id},
            'settings': settings,
            'overrides': fragments,
            'references': sorted({*fragments.values(), *(a['path'] for a in assets)}),
            'reference_paths': sorted(written),
            'assets': assets,
            'dotfiles': dotfiles,  # the folder as config.yaml names it, or None
            'system_overrides': system_overrides,
            'packages': packages,
            'integrations': integrations,
        }
    }
