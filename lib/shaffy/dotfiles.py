"""The person's own chezmoi source, the supplement's `dotfiles`, applied after Shaffy OS's own home.

Every chezmoi run on it reads an operation's capture, never the live folder, with a
configuration Shaffy OS writes, its own state file and cache, and no Shaffy OS variable in its
environment. Before chezmoi reads the tree, its captured file names are scanned for what this
version refuses (`refusals`). The targets chezmoi would apply from it are then compared with
everything Shaffy OS can manage under any settings (`_claims`), and each overlap is refused
with a message that names the target and its owner (`_conflicts`).
"""

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import tomllib
from typing import Literal, NamedTuple

from . import desktop_key
from .capture import CaptureError, capture, tree
from .selection import resolve, state_dir

# Every feature this version refuses, with the reason and what to do instead.
REFUSED = {
    'script': (
        'is a script; this version runs none, because a script can change any file without the apply '
        'copying it first (declare packages in packages.extra and tools in overrides.mise instead)'
    ),
    'encrypted': (
        'is encrypted; secrets stay outside the supplement, and decrypting needs an identity the apply '
        'does not have (keep the file outside the dotfiles)'
    ),
    'external': (
        'declares externals, whose download at apply time is not captured and whose targets are unknown '
        'until it runs (add the files to the dotfiles instead)'
    ),
    'external folder': (
        'is in an external_ folder, whose entries chezmoi reads without their prefixes, so this version '
        'cannot check them (drop external_, and start a name that looks like a prefix with literal_)'
    ),
    'removal list': (
        'removes by pattern, which can reach targets Shaffy OS creates later '
        '(name each file to remove with a remove_ entry instead)'
    ),
    'nested root': (
        'is a .chezmoiroot below the top of the dotfiles, so chezmoi could read its root twice '
        '(keep the one at the top alone)'
    ),
    'unknown special': (
        'is a special name this version does not know, so it cannot tell what chezmoi does with it (drop or rename it)'
    ),
}
# Where the refused features show in names: special names at any depth, matched by their start,
# and attributes of a source name. chezmoi ignores every other name that starts with a dot, with
# all it holds; of those that start with .chezmoi, this version knows the ones KNOWN matches, which
# chezmoi reads as ignore rules, data, templates, the root or the version, or (.chezmoi.<format>.tmpl)
# only on `chezmoi init`.
SPECIAL = {'.chezmoiscripts': 'script', '.chezmoiexternal': 'external', '.chezmoiremove': 'removal list'}
KNOWN = re.compile(r'\.chezmoi(ignore(\.tmpl)?|data(\.(json|jsonc|toml|yaml|yml))?|templates|root|version|\..+\.tmpl)')
ATTRIBUTES = {'run': 'script', 'encrypted': 'encrypted', 'external': 'external folder'}

# chezmoi's prefixes in the order it reads them, each at most once, alternatives joined by |
# ("Source state attributes" in its reference). A file's first prefix may give its type, which
# decides the prefixes that may follow it.
FOLDER_PREFIXES = 'remove external exact private readonly dot'
FILE_PREFIXES = 'encrypted private readonly empty executable dot'
TYPED_FILE_PREFIXES = {
    'create': 'create encrypted private readonly empty executable dot',
    'modify': 'modify encrypted private readonly executable dot',
    'remove': 'remove dot',
    'run': 'run once|onchange before|after dot',
    'symlink': 'symlink dot',
}

OVERRIDE = re.compile(r'dig "(\w+)" "" \(includeTemplate "shaffy\.json" \. \| fromJson\)\.shaffy\.overrides')
GLOB = re.compile(r'[*?[]')


class Pass(NamedTuple):
    """One chezmoi source as the home apply runs it: Shaffy OS's first, then the dotfiles'.

    `command` is chezmoi with its configuration and source; a subcommand follows. `env` is the
    whole environment it runs with: Shaffy OS's carries the operation marker, the dotfiles'
    carries no SHAFFY_ variable at all.
    """

    name: Literal['shaffy', 'dotfiles']
    command: tuple[str, ...]
    env: Mapping[str, str]

    def run(self, *arguments):
        return subprocess.run(
            [*self.command, *arguments], capture_output=True, text=True, env=dict(self.env), check=False
        )


def prepare(checkout, shaffy, read_only=False):
    """The dotfiles pass for SHAFFY, the loader's output for a capture, and every reason to refuse it.

    (None, []) without a `dotfiles` setting. Otherwise, each step only when the one before found
    nothing: the captured file names against what this version refuses; `chezmoi managed` of the
    captured tree with the pass's own configuration, in this machine's environment; its targets
    against Shaffy OS's claims. Writes only into the operation's run folder. The pass comes back
    with overlap problems too. READ_ONLY, for check, diff and status, gives the pass a copy of the
    dotfiles' state there, so a preview never holds the state an apply is about to write.
    """
    reference = shaffy['dotfiles']
    if not reference:
        return None, []
    supplement = Path(shaffy['selection']['path'])
    manifest = supplement.parent / 'manifest.json'
    if not manifest.is_file():
        raise ValueError(f'the dotfiles are applied only from a capture, not from {supplement}')
    prefix = f'supplement/{reference}/'
    names = [name.removeprefix(prefix) for name in json.loads(manifest.read_text())['files'] if name.startswith(prefix)]
    run = supplement.parent.parent / 'dotfiles'
    try:
        problems = _scanned(supplement / reference, names)
        if problems:
            return None, problems
        state = state_path()
        if read_only:
            run.mkdir(mode=0o700, exist_ok=True)
            if state.exists():
                shutil.copyfile(state, run / state.name)
            state = run / state.name
        cache = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'shaffy/dotfiles'
        chezmoi = _pass(supplement / reference, shaffy['settings'], run / 'chezmoi.json', state, cache, os.environ)
        listed = chezmoi.run(
            'managed', '--include', 'dirs,files,symlinks,remove', '--format', 'json', '--path-style', 'all'
        )
        if listed.returncode:
            return chezmoi, ['dotfiles: chezmoi cannot read them: ' + listed.stderr.strip()[-800:]]
        return chezmoi, _conflicts(_claims(checkout, shaffy), _entries(listed.stdout))
    except (OSError, UnicodeDecodeError, subprocess.CalledProcessError) as error:
        return None, [_unchecked(error)]


@contextmanager
def preview(checkout, supplement=None, without=False):
    """(the dotfiles' reference, *prepare()) for the live or an explicit selection, from a capture removed on exit.

    For `status` and the plain `diff`: they show what an apply of the selection as it is now
    would apply. The capture is private, under Shaffy OS's state folder. Raises SelectionError,
    SettingsError or CaptureError as capture() does.
    """
    from .loader import load  # PyYAML and jsonschema, which the render sandbox's import of this module skips

    state_dir().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='preview-', dir=state_dir()) as state:
        made = capture(checkout, resolve(supplement, without), state, 'preview', load, dotfiles=True)
        shaffy = json.loads((Path(made['directory']) / 'settings.json').read_text())['shaffy']
        yield shaffy['dotfiles'], *prepare(checkout, shaffy, read_only=True)


def state_path():
    """The dotfiles' chezmoi state, beside Shaffy OS's own in its state folder."""
    return state_dir() / 'dotfiles.boltdb'


def leftovers(home):
    """Plain chezmoi's configuration files and source that still exist: a second way to change the home."""
    config, source = _plain_chezmoi(home)
    return sorted(config.glob('chezmoi.*')) + ([source] if source.exists() else [])


def _plain_chezmoi(home):
    """Plain chezmoi's default configuration folder and source, where XDG puts them for HOME."""
    config = Path(os.environ.get('XDG_CONFIG_HOME') or Path(home) / '.config') / 'chezmoi'
    return config, Path(os.environ.get('XDG_DATA_HOME') or Path(home) / '.local/share') / 'chezmoi'


def sandbox_pass(shaffy, work):
    """The dotfiles pass inside the render sandbox, with its configuration, state and cache in WORK.

    SHAFFY is the loader's output as the sandbox's own Shaffy OS pass sees it, the captured
    supplement mounted read-only. The tree is walked there, as the capture's manifest lies outside
    the sandbox. No overlap check: the synthetic host would evaluate the person's ignore rules
    differently from this machine.
    """
    reference = shaffy['dotfiles']
    if not reference:
        return None, []
    supplement = Path(shaffy['selection']['path'])
    try:
        problems = _scanned(supplement / reference, tree(supplement / reference, supplement))
    except CaptureError as error:
        return None, [f'dotfiles: {error}']
    except (OSError, UnicodeDecodeError) as error:
        return None, [_unchecked(error)]
    if problems:
        return None, problems
    work = Path(work)
    chezmoi = _pass(
        supplement / reference,
        shaffy['settings'],
        work / 'dotfiles.json',
        work / 'dotfiles.boltdb',
        work / 'dotfiles-cache',
        os.environ,
    )
    return chezmoi, []


def _scanned(folder, names):
    """Every refusal for the dotfiles FOLDER from its file NAMES: a .chezmoiroot below the top or
    pointing out of FOLDER, and what refusals() finds under the root it names.

    The pass reads FOLDER itself as its source, so chezmoi resolves the .chezmoiroot once.
    """
    nested = [name for name in names if name != '.chezmoiroot' and PurePosixPath(name).name == '.chezmoiroot']
    if nested:
        return refusals(nested)
    if '.chezmoiroot' not in names:
        return refusals(names)
    named = (folder / '.chezmoiroot').read_text(encoding='utf-8').strip()
    root = os.path.normpath(named)
    if os.path.isabs(named) or root == '..' or root.startswith('../'):
        return [f'dotfiles: .chezmoiroot names {named!r}, outside the dotfiles; name a folder inside them']
    prefix = '' if root == '.' else root + '/'
    return refusals([name.removeprefix(prefix) for name in names if name.startswith(prefix)])


def _unchecked(error):
    """The refusal for dotfiles that could not be read or checked at all."""
    detail = getattr(error, 'stderr', None) or ''
    return f'dotfiles: they cannot be checked: {error} {detail.strip()[-800:]}'.strip()


def _pass(source, settings, config, state, cache, env):
    """Write the pass's whole configuration to CONFIG, privately as it holds the settings, and return the pass.

    persistentState and cacheDir are its own; mode "file" keeps chezmoi from linking targets into
    a capture that is pruned later; data is {"shaffy": {"settings": SETTINGS}}, which wins over a
    `shaffy` key in the person's .chezmoidata. --no-tty turns any prompt into an error, not a wait.
    """
    config = Path(config)
    config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    text = json.dumps(
        {
            'persistentState': str(state),
            'cacheDir': str(cache),
            'mode': 'file',
            'data': {'shaffy': {'settings': settings}},
            # Only `chezmoi init` reads a .chezmoi.<format>.tmpl, which the dotfiles may keep.
            'warnings': {'configFileTemplateHasChanged': False},
        }
    )
    descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(text)
    command = ('chezmoi', '--config', str(config), '--config-format', 'json', '--source', str(source), '--no-tty')
    return Pass('dotfiles', command, {name: value for name, value in env.items() if not name.startswith('SHAFFY_')})


def refusals(names):
    """What this version refuses in a dotfiles tree: one message per path; NAMES are its files, relative to its root.

    Read from the names alone, in chezmoi's prefix order, so a refused feature never runs: no
    download, no decryption, no script. Ignored entries count too, so the answer does not depend
    on the machine that evaluates `.chezmoiignore`.
    """
    return [f'dotfiles: {name} {REFUSED[feature]}' for name in names if (feature := _feature(name))]


def _feature(name):
    parts = PurePosixPath(name).parts
    for depth, part in enumerate(parts):
        if part == '.chezmoiroot' and depth:
            return 'nested root'
        if part.startswith('.'):
            special = next((feature for start, feature in SPECIAL.items() if part.startswith(start)), None)
            unknown = part.startswith('.chezmoi') and not KNOWN.fullmatch(part)
            return special or ('unknown special' if unknown else None)
        for attribute in _attributes(part, folder=depth < len(parts) - 1):
            if attribute in ATTRIBUTES:
                return ATTRIBUTES[attribute]
    return None


def _attributes(name, folder):
    """The attributes one source name carries, in the order chezmoi reads them: each at most once,
    until no prefix left in that order follows (the rest is the name). `literal_` is none, so it ends them."""
    if folder:
        order = FOLDER_PREFIXES
    else:
        order = next(
            (typed for kind, typed in TYPED_FILE_PREFIXES.items() if name.startswith(kind + '_')), FILE_PREFIXES
        )
    found = []
    for slot in order.split():
        prefix = next((prefix for prefix in slot.split('|') if name.startswith(prefix + '_')), None)
        if prefix:
            found.append(prefix)
            name = name.removeprefix(prefix + '_')
    return tuple(found)


class Mode(NamedTuple):
    """A folder's private_ and readonly_ attributes."""

    private: bool
    readonly: bool


@dataclass(frozen=True)
class Claim:
    """A target Shaffy OS can manage, and the mechanism a refusal names for it."""

    kind: Literal['file', 'folder']
    owner: Literal['upstream', 'overrides', 'assets', 'integration']
    via: str  # upstream: where it comes from; overrides and integration: the name; assets: the target
    # A folder home/ names, whose mode Shaffy OS sets: the dotfiles must repeat it. Shaffy OS
    # sets the mode of no other folder, such as one that only holds an asset.
    mode: Mode | None = None


@dataclass(frozen=True)
class Claims:
    """Everything Shaffy OS can manage in the home under any settings, as targets relative to the home."""

    paths: Mapping[str, Claim]  # files, and folders including every folder on the way to a claim
    patterns: Mapping[str, Claim]  # globs owned with everything they match: integration X/**, state, cache


@dataclass(frozen=True)
class Entry:
    """One target the dotfiles pass manages here, from `chezmoi managed --format json --path-style all`."""

    target: str
    source: str  # sourceRelative, to name it in a message
    kind: Literal['file', 'folder', 'symlink', 'remove']
    private: bool = False
    readonly: bool = False
    exact: bool = False


# How a refusal names each owner, and what the person does instead.
OWNERS = {
    'upstream': ('upstream: {via}', 'drop it from the dotfiles'),
    'overrides': ('overrides.{via}', 'put its content in that fragment instead'),
    'assets': ('assets: ~/{via}', 'keep it in one of the two'),
    'integration': (
        'integration {via}',
        'drop it from the dotfiles, or release the integration (integrations.{via}: released)',
    ),
}


def _claims(checkout, shaffy):
    """Shaffy OS's claims under any settings, derived from the checkout and SHAFFY (the loader's output).

    - every entry of home/ that chezmoi reads as a target, mapped by one `chezmoi target-path`
      call; a folder carries its mode from its name, an exact_ one owns all it holds, and a
      template that reads `.shaffy.overrides.NAME` is owned by that fragment;
    - every integration's targets, except those of one the settings release: the person has
      taken them over, so they yield, home/ entries included;
    - home/.chezmoiremove's lines, desktop_key.TARGET and the assets' targets;
    - Shaffy OS's state folder, its chezmoi state's folder and its cache, and plain chezmoi's
      configuration and source folders, with everything they hold;
    - every folder on the way to any of these, as a plain folder unless it is claimed already.
    """
    home = Path(checkout) / 'home'
    integrations = shaffy['integrations'].items()
    held = {target: name for name, found in integrations if found['state'] != 'released' for target in found['targets']}
    released = [target for _, found in integrations if found['state'] == 'released' for target in found['targets']]
    sources = sorted(
        path
        for path in home.rglob('*')
        if not any(part.startswith('.') or part == '__pycache__' for part in path.relative_to(home).parts)
    )
    paths, patterns = {}, {}
    for source, target in zip(sources, _target_paths(home, sources), strict=True):
        if _matched(target, released):
            continue
        integration = next((name for pattern, name in held.items() if _matched(target, [pattern])), None)
        fragment = source.suffix == '.tmpl' and OVERRIDE.search(source.read_text(encoding='utf-8'))
        if integration:
            owner = ('integration', integration)
        elif fragment:
            owner = ('overrides', fragment[1])
        else:
            owner = ('upstream', f'home/{source.relative_to(home)}')
        if source.is_dir():
            attributes = _attributes(source.name, folder=True)
            paths[target] = Claim('folder', *owner, Mode('private' in attributes, 'readonly' in attributes))
            if 'exact' in attributes:
                patterns[f'{target}/**'] = Claim('folder', *owner)
        else:
            paths[target] = Claim('file', *owner)
    lines = [line.strip() for line in (home / '.chezmoiremove').read_text(encoding='utf-8').splitlines()]
    more = [(target, 'integration', name) for target, name in held.items()]
    more += [(line, 'upstream', 'home/.chezmoiremove') for line in lines if line and not line.startswith('#')]
    more += [(desktop_key.TARGET, 'upstream', 'lib/shaffy/desktop_key.py')]
    more += [(asset['target'], 'assets', asset['target']) for asset in shaffy['assets']]
    more += [(f'{folder}/**', 'upstream', via) for folder, via in _folders(checkout)]
    for target, owner, via in more:
        if GLOB.search(target):
            patterns[target] = Claim('folder', owner, via)
        else:
            paths.setdefault(target, Claim('file', owner, via))
    # A pattern's parents are its root (X for X/**) and the folders above it.
    for target, claim in sorted([*paths.items(), *patterns.items()]):
        for folder in map(str, PurePosixPath(target).parents):
            if folder != '.' and not GLOB.search(folder):
                paths.setdefault(folder, Claim('folder', claim.owner, claim.via))
    return Claims(paths, patterns)


def _target_paths(home, sources):
    """The target of each of SOURCES in Shaffy OS's source HOME, relative to the home.

    One chezmoi call that evaluates no template, needs no settings and writes no state, with a
    configuration of its own so that the person's is never read.
    """
    with tempfile.TemporaryDirectory(prefix='shaffy-claims-') as work:
        config = Path(work) / 'chezmoi.json'
        config.write_text(json.dumps({'persistentState': str(Path(work) / 'state.boltdb')}))
        listed = subprocess.run(
            ['chezmoi', '--config', str(config), '--config-format', 'json', '--source', str(home)]
            + ['--destination', work, 'target-path', *map(str, sources)],
            capture_output=True,
            text=True,
            check=True,
        )
    return [str(Path(line).relative_to(work)) for line in listed.stdout.splitlines()]


def _folders(checkout):
    """Shaffy OS's state folders and cache, and plain chezmoi's configuration and source folders, when in the home.

    Shaffy OS's chezmoi state is where the checkout's chezmoi.toml puts it, whatever XDG_STATE_HOME says.
    """
    home = Path.home()
    chezmoi_state = tomllib.loads((Path(checkout) / 'chezmoi.toml').read_text(encoding='utf-8'))['persistentState']
    cache = Path(os.environ.get('XDG_CACHE_HOME') or home / '.cache')
    config, source = _plain_chezmoi(home)
    folders = {
        Path(chezmoi_state).expanduser().parent: "Shaffy OS's chezmoi state",
        state_dir(): "Shaffy OS's state",
        cache / 'shaffy': "Shaffy OS's cache",
        config: "plain chezmoi's configuration, a second way to change the home",
        source: "plain chezmoi's source, a second way to change the home",
    }
    return [(folder.relative_to(home), via) for folder, via in folders.items() if folder.is_relative_to(home)]


def _matched(target, patterns):
    """Whether TARGET is one of PATTERNS: a literal names itself, a glob what it matches (never `X` for `X/**`)."""
    return any(PurePosixPath(target).full_match(pattern) for pattern in patterns)


def _entries(listing):
    """Parse `managed --format json --path-style all`: the kind from the source on disk and its name.

    An entry without a sourceRelative, a file an exact_ folder prunes, has no source of its own:
    the folder's own entry stands for it.
    """
    entries = []
    for target, found in json.loads(listing).items():
        if not found.get('sourceRelative'):
            continue
        folder = os.path.isdir(found['sourceAbsolute'])
        attributes = _attributes(PurePosixPath(found['sourceRelative']).name, folder)
        if 'remove' in attributes:
            kind = 'remove'
        elif folder:
            kind = 'folder'
        else:
            kind = 'symlink' if 'symlink' in attributes else 'file'
        private, readonly, exact = 'private' in attributes, 'readonly' in attributes, 'exact' in attributes
        entries.append(Entry(target, found['sourceRelative'], kind, private, readonly, exact))
    return entries


def _conflicts(claims, entries):
    """One message per dotfiles entry that would reach a Shaffy OS target, naming the target and its owner.

    1. a claimed file at the entry's target;
    2. a claimed folder where the entry is not a folder, is exact, or lacks the folder's mode
       where Shaffy OS sets one, or is read-only where Shaffy OS only writes into it;
    3. the entry lies strictly below a claimed pattern (full_match never matches its root);
    and a remove_ entry at a claimed target or above one, as every folder on the way to a claim is claimed.
    """
    problems = []
    for entry in entries:
        pattern = next(
            (pattern for pattern in claims.patterns if PurePosixPath(entry.target).full_match(pattern)), None
        )
        claim = claims.patterns[pattern] if pattern else claims.paths.get(entry.target)
        if claim is None:
            continue
        owner, advice = (text.format(via=claim.via) for text in OWNERS[claim.owner])
        if entry.kind == 'remove':
            problem = f'is or holds what Shaffy OS manages ({owner}); drop {entry.source}, which would remove it'
        elif pattern:
            problem = f"lies in ~/{pattern}, which is Shaffy OS's ({owner}); {advice}"
        elif claim.kind == 'file':
            problem = f"is Shaffy OS's ({owner}); {advice}"
        elif entry.kind != 'folder':
            problem = (
                f'is a folder Shaffy OS manages ({owner}); drop the {entry.kind} {entry.source} that would replace it'
            )
        elif entry.exact:
            problem = f'is a folder Shaffy OS manages ({owner}); exact_ in {entry.source} would delete its files'
        elif claim.mode is not None and (entry.private, entry.readonly) != claim.mode:
            prefixes = (
                'private_' * claim.mode.private + 'readonly_' * claim.mode.readonly or 'neither private_ nor readonly_'
            )
            problem = f'is a folder Shaffy OS names with {prefixes} ({owner}); give {entry.source} the same'
        elif claim.mode is None and entry.readonly:
            problem = f'is a folder Shaffy OS writes into ({owner}); readonly_ in {entry.source} would stop it'
        else:
            continue
        problems.append(f'dotfiles: ~/{entry.target} {problem}')
    return problems
