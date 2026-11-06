"""Full settings loader: parse, validate and merge defaults with a supplement.

PyYAML parses (YAML 1.2 core typing, duplicate keys rejected), python-jsonschema
validates the structure, and hand-written checks cover what a schema cannot.
"""

import copy
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
    """Defaults carry schema_version but no supplement_id or minimum_release."""
    result = copy.deepcopy(schema)
    result['required'] = ['schema_version']
    result['properties'].pop('supplement_id')
    result['properties'].pop('minimum_release')
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


def check_reference(root, reference, label):
    """A referenced file must exist inside the supplement, after resolving symlinks."""
    if os.path.isabs(reference):
        raise SettingsError([f'{label}: {reference!r} must be relative to the supplement'])
    root = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root, reference))
    if os.path.commonpath([root, target]) != root:
        raise SettingsError([f'{label}: {reference!r} resolves outside the supplement'])
    if not os.path.isfile(target):
        raise SettingsError([f'{label}: {reference!r} does not exist in the supplement'])
    return target


# restic-home writes its verification marker here, so a backup must include it.
BACKUP_MARKER = ('.local', 'state', 'shaffy', 'home-backup')


def _effective_problems(settings):
    servers = settings.get('ai', {}).get('mcp_servers', {})
    problems = []
    battery = settings.get('power', {}).get('low_battery_hibernate')
    if battery and not battery['low_percent'] > battery['critical_percent'] > battery['action_percent']:
        problems.append('power.low_battery_hibernate: require low_percent > critical_percent > action_percent')
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


def load(checkout, supplement=None, without=False, home=None):
    """Resolve, parse, validate and merge. Raises SelectionError or SettingsError."""
    checkout = Path(checkout)
    selection = resolve(supplement, without, home)
    schema = json.loads((checkout / 'schema/supplement.schema.json').read_text())
    defaults_path = checkout / 'defaults/config.yaml'
    defaults = parse(defaults_path)
    problems = validate(defaults, defaults_schema(schema), str(defaults_path))
    overrides, supplement_id = {}, None
    if selection['mode'] == 'supplement':
        config = Path(selection['path']) / 'config.yaml'
        overrides = parse(config)
        # Too old a checkout would also report the newer fields as unknown keys; say only that.
        too_old = release_problem(checkout, overrides.get('minimum_release'))
        problems += [f'{config}: {too_old}'] if too_old else validate(overrides, schema, str(config))
        supplement_id = overrides.get('supplement_id')
    if problems:
        raise SettingsError(problems)
    settings = merge(
        {k: v for k, v in defaults.items() if k not in IDENTITY_KEYS},
        {k: v for k, v in overrides.items() if k not in IDENTITY_KEYS},
    )
    problems = _effective_problems(settings)
    fragments, written, system_overrides = {}, set(), {}
    for name, reference in settings.pop('overrides', {}).items():
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
    for target, value in sorted(settings.pop('assets', {}).items()):
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
            'system_overrides': system_overrides,
            'packages': packages,
            'integrations': integrations,
        }
    }
