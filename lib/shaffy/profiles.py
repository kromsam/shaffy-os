"""Profiles: small public bundles of settings, vendored into a supplement (docs/PROFILES.md).

A supplement's config.yaml lists the profiles it uses, in merge order. Their copies live in its
profiles/ folder, profiles/OWNER/NAME/, and profiles/lock.json records where each came from: the
repository, the folder in it, the ref it follows, the commit and the SHA-256 of every file.

This module holds what the loader needs: what a profile may set and why, and whether each listed
copy still holds what was vendored. fastload hashes it, so it uses the standard library only, reads
no network and writes nothing. The loader's parse, validate and release check are passed in, as
packages.load_groups takes parse, so it never imports the loader. vendor.py fetches and writes.
"""

import copy
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys

FOLDER = 'profiles'
LOCK = 'profiles/lock.json'
LOCK_VERSION = 1
MANIFEST = 'profile.yaml'
README = 'README.md'
ID = re.compile(r'^([a-z0-9][a-z0-9-]{0,38})/([a-z0-9][a-z0-9-]{0,62})$')
COMMIT = re.compile(r'^[0-9a-f]{40}$')
DIGEST = re.compile(r'^[0-9a-f]{64}$')
DESCRIPTION_LIMIT = 200

# Each tool's profile fragments: one file per profile, NN its place in config.yaml's list. Every
# place lies under .config/, where home/dot_config/.chezmoiremove.tmpl deletes a file the settings
# no longer render. Noctalia and mise read their folders in name order, so the names carry the
# order; the others load an index template that lists them.
PLACES = {
    'niri': '.config/niri/profiles/{position:02d}-{slug}.kdl',
    'fish': '.config/fish/profiles/{position:02d}-{slug}.fish',
    'alacritty': '.config/alacritty/profiles/{position:02d}-{slug}.toml',
    'noctalia': '.config/noctalia/96-profile-{position:02d}-{slug}.toml',
    'mise': '.config/mise/conf.d/80-profile-{position:02d}-{slug}.toml',
}

# The keys of a profile.yaml that are not settings: the format, the description and what it needs.
PROFILE_KEYS = ('schema_version', 'description', 'minimum_release', 'requires')

# What a profile may set, section by section; None takes the whole section. A profile adds things:
# fragments, files, packages, editor extras, plugins and servers. It never sets a value the machine
# has only one of.
PROFILE_SECTIONS = {
    'ai': ('mcp_servers', 'claude_plugins'),
    'assets': None,
    'lazyvim': None,
    'overrides': None,
    'packages': ('extra',),
}

# Everything else, with the reason explain() gives a profile.yaml that sets it.
PROFILE_EXCLUDED = {
    'supplement_id': 'it names one installation',
    'profiles': 'only config.yaml lists profiles; a profile does not pull in others',
    'integrations': 'integration states are requests against this machine; a profile lists what it requires',
    'dotfiles': "a whole chezmoi source of one person's, applied as one second pass",
    'identity': 'it names a person',
    'tailnet': "it names one person's network and node",
    'kde_connect': "it names one machine's interface and network",
    'localsend': "it names one machine's interface and network",
    'backup': "it names one person's server, account and keys",
    'folders': "folder overrides adapt one installation's home",
    'login': "it changes one machine's login security, as root",
    'power': "it sizes swap and battery limits for one machine's hardware, as root",
    'desktop.browser': (
        'the default browser is one choice per installation, one value two profiles would fight over; '
        'a profile can install a browser with packages.extra'
    ),
    'desktop.keyboard': (
        'the layouts a person types are theirs, one value two profiles would fight over; a keyboard '
        "model's map goes in overrides.keyboard_hwdb"
    ),
    'desktop.displays': "display geometry is one machine's",
    'desktop.wallpaper': 'it names paths in one home; ship the picture as an asset instead',
    'desktop.lock_screen': 'it names paths in one home; ship the picture as an asset instead',
    'desktop.boot_splash': 'root rebuilds the boot images and the disk password prompt with it',
    'ai.pstack': "one installation's switch, one value two profiles would fight over",
    'packages.omit': 'omissions adapt the public desktop to one installation',
    'packages.remove': "a removal in someone else's file would uninstall on their next update",
}

# Asset targets a profile may never write: a profile shares preferences, never access, and never
# the state of the tools that manage the home.
PROFILE_REFUSED_TARGETS = (
    '~/.ssh/',
    '~/.gnupg/',
    '~/.pki/',
    '~/.password-store/',
    '~/.local/share/keyrings/',
    '~/.config/chezmoi/',
    '~/.local/state/shaffy/',
)

# A hwdb match line names one unit, not a keyboard model, when it matches a port position (phys
# starting usb-, pci- or a bus address), a unique id or a serial. systemd's own maps use phys only
# for device names (gpio-keys/input0, sp/serio*/input*), which stay allowed; a test holds both.
HWDB_UNIT = re.compile(r'(?i):phys:(usb-|pci-|[0-9a-f]{4}:)|:uniq:|serial')


@dataclass(frozen=True)
class Locked:
    """One profiles/lock.json entry: where a vendored copy came from and what it must hold."""

    id: str  # OWNER/NAME, lower case; the folder profiles/OWNER/NAME
    source: str  # https URL of the Git repository, as git fetches it
    path: str  # the profile's folder in that repository, one level deep: its NAME
    ref: str  # what update and outdated follow: HEAD, a branch, a tag, or a commit (pinned)
    commit: str  # 40 hex: what was vendored
    files: dict  # path inside the folder -> SHA-256; profile.yaml always among them


@dataclass(frozen=True)
class Vendored:
    """A listed profile as the supplement holds it, its bytes verified against the lock."""

    locked: Locked
    folder: Path  # absolute: <supplement>/profiles/OWNER/NAME
    prefix: str  # its folder relative to the supplement: profiles/OWNER/NAME/


def parse_id(text):
    """OWNER and NAME of a profile ID; raises ValueError naming the expected form."""
    match = ID.match(text) if isinstance(text, str) else None
    if not match:
        raise ValueError(f'{text!r} is not a profile ID: OWNER/NAME in lower-case letters, digits and hyphens')
    return match.group(1), match.group(2)


def place(tool, position, profile_id):
    """The home target of a profile's fragment for TOOL, NN its 1-based place in the list."""
    return PLACES[tool].format(position=position, slug=profile_id.replace('/', '-'))


def places():
    """{tool: glob} of every target PLACES can produce, for the removal template and the claims."""
    return {tool: pattern.replace('{position:02d}-{slug}', '*') for tool, pattern in PLACES.items()}


def issues(locked):
    """Where to tell a profile's author: its GitHub issues page, else its repository URL."""
    source = locked.source.rstrip('/')
    return source + '/issues' if source.startswith('https://github.com/') else source


# -- the format ------------------------------------------------------------------------------


def profile_schema(schema):
    """A profile.yaml: PROFILE_SECTIONS of the supplement schema, copied so their patterns and
    descriptions never drift, plus schema_version, description, minimum_release and requires
    (items: the integrations' name pattern). schema/profile.schema.json is this, generated."""
    properties = {
        'schema_version': copy.deepcopy(schema['properties']['schema_version']),
        'description': {
            'description': 'What the profile sets up, in one line, for the person who reads the review.',
            'type': 'string',
            'minLength': 1,
            'maxLength': DESCRIPTION_LIMIT,
            'pattern': '^[^\\u0000-\\u001f\\u007f]+$',
        },
        'minimum_release': copy.deepcopy(schema['properties']['minimum_release']),
        'requires': {
            'description': (
                'Integrations from defaults/integrations.yml that config.yaml must set to managed for this '
                'profile to apply; a profile never sets their state.'
            ),
            'type': 'array',
            'minItems': 1,
            'uniqueItems': True,
            'items': {'type': 'string', 'pattern': schema['properties']['integrations']['propertyNames']['pattern']},
        },
    }
    for section, allowed in PROFILE_SECTIONS.items():
        copied = copy.deepcopy(schema['properties'][section])
        if allowed is not None:
            copied['properties'] = {name: copied['properties'][name] for name in allowed}
        properties[section] = copied
    used = json.dumps(properties)
    return {
        '$schema': schema['$schema'],
        'title': 'Shaffy OS profile',
        'description': (
            'A profile.yaml: a small public bundle of settings a supplement vendors under profiles/OWNER/NAME/ '
            'and lists in config.yaml (docs/PROFILES.md). It adds fragments, files, packages, editor extras, '
            'plugins and MCP servers; it never sets a value the machine has only one of.'
        ),
        'type': 'object',
        'additionalProperties': False,
        'required': ['schema_version', 'description'],
        'properties': properties,
        '$defs': {name: value for name, value in schema['$defs'].items() if f'#/$defs/{name}' in used},
    }


def explain(key, profile_id=None):
    """Why a profile may not set KEY, and the way out; None when KEY is not in PROFILE_EXCLUDED
    (the schema then reports it as unknown). Without PROFILE_ID (an author's check) the command
    names no ID."""
    reason = PROFILE_EXCLUDED.get(key)
    if reason is None:
        return None
    command = './bin/shaffy profile propose' + (f' {profile_id}' if profile_id else '') + f' --to shaffy --key {key}'
    return (
        f'{key}: a profile cannot set this: {reason}. Set it in config.yaml. If profiles should be able to '
        f'share it, that is a Shaffy OS change: {command} drafts the issue.'
    )


def _excluded(data):
    """The PROFILE_EXCLUDED keys DATA sets, dotted, in file order."""
    found = []
    for key, value in data.items():
        if key in PROFILE_EXCLUDED:
            found.append(key)
        elif isinstance(value, dict):
            found += [f'{key}.{sub}' for sub in value if f'{key}.{sub}' in PROFILE_EXCLUDED]
    return found


def hwdb_unit_matches(text):
    """1-based numbers of TEXT's match lines (unindented, not comments) that HWDB_UNIT finds."""
    return [
        number
        for number, line in enumerate(text.splitlines(), 1)
        if line and not line[0].isspace() and not line.startswith('#') and HWDB_UNIT.search(line)
    ]


def read_profile(folder, schema, label, *, parse, validate, release_problem, profile_id=None):
    """Parse and check FOLDER/profile.yaml; return (data, problems).

    PARSE and VALIDATE are the loader's, RELEASE_PROBLEM its release check bound to the checkout.
    Order: too new a minimum_release alone; else every PROFILE_EXCLUDED key with explain(); else
    the schema (profile_schema(schema)); then every asset target under PROFILE_REFUSED_TARGETS.
    References and the keyboard map's text are the loader's (_layer_files), which follows links.
    """
    data = parse(Path(folder) / MANIFEST)
    too_new = release_problem(data.get('minimum_release'))
    if too_new:
        return data, [f'{label}: {too_new}']
    problems = [f'{label}: {explain(key, profile_id)}' for key in _excluded(data)]
    if problems:
        return data, problems
    problems = validate(data, profile_schema(schema), label)
    if problems:
        return data, problems
    for target in data.get('assets', {}):
        refused = next((prefix for prefix in PROFILE_REFUSED_TARGETS if (target + '/').startswith(prefix)), None)
        if refused:
            problems.append(
                f'{label}: assets.{target}: a profile may not write under {refused}: it shares preferences, '
                'never access or the state of the tools that manage the home'
            )
    return data, problems


if __name__ == '__main__':  # regenerate schema/profile.schema.json from the supplement schema
    _schema = json.loads((Path(__file__).resolve().parents[2] / 'schema/supplement.schema.json').read_text())
    sys.stdout.write(json.dumps(profile_schema(_schema), indent=2) + '\n')
