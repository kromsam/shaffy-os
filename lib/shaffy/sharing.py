"""The way back upstream from a supplement's vendored profiles (docs/PROFILES.md#fix-it-upstream).

propose drafts a person's hand edits of a vendored copy as a patch for the profile's author, and
propose_shaffy drafts a Shaffy OS issue for a need no profile can express. findings, summary and
behind give check, status and outdated their profile lines, each naming the command or the author
that settles what it found. This module reads profiles/ and writes nothing: vendor.py fetches and
writes, and the person sends every draft.
"""

import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import textwrap
import tomllib
from urllib.parse import quote

from . import literals, profiles
from .source import release
from .vendor import (
    LIMITS,
    LOCK_TEMPORARY,
    STAGING,
    ProfileError,
    _config,
    _contents,
    _manifest,
    _place,
    _read_lock,
    _resolve,
)

# Where a need no profile can express goes: Shaffy OS's Configuration form, whose fields a draft follows.
SHAFFY_ISSUES = 'https://github.com/kromsam/shaffy-os/issues'
FORM = '.github/ISSUE_TEMPLATE/configuration.yml'
MECHANISM = 'A mechanism profiles need'  # the form's "Where it should go" option for such a need


def propose(root, checkout, profile_id, upstream):
    """(patch, header lines): the copy's hand edits as a patch git apply takes in the author's
    repository at the locked commit, and where to send it. The header names each personal-looking
    value in the added lines by its rule and line, never the value."""
    root = Path(root)
    locked = _read_lock(root).get(profile_id)
    if locked is None:
        raise ProfileError(f'{profile_id} is not vendored here; nothing to propose.')
    folder = _place(root / profiles.FOLDER, profile_id)
    if not folder.is_dir():
        raise ProfileError(f'{folder} is missing; git restore {profiles.FOLDER}/ brings it back.')
    current = _contents(folder)
    if current == locked.files:
        raise ProfileError(f'nothing to propose: {profiles.FOLDER}/{profile_id} is as vendored at {locked.commit[:7]}.')
    links = [name for name, digest in current.items() if digest == 'link']
    if links:
        raise ProfileError(
            f'{", ".join(links)} in {folder} {"is a link" if len(links) == 1 else "are links"}; '
            'a profile holds plain files.'
        )
    try:
        fetched = upstream.tree(locked.source, locked.commit, locked.path, LIMITS)
    except ValueError as error:
        raise ProfileError(f'{profile_id}: {error}') from None
    old = {name: fetched[name] for name in locked.files if name in fetched}
    new = {name: (folder / name).read_bytes() for name in current}
    patch = _patch(locked.path, old, new)
    hits = _personal(patch, literals.static_rules() + literals.environment_rules(checkout))
    source = locked.source.rstrip('/').removesuffix('.git')
    header = [
        f'A fix for {profile_id} against {locked.source} at {locked.commit[:7]}.',
        *_scanned(hits, 'the added lines'),
    ]
    if source.startswith('https://github.com/'):
        title = quote(f'{locked.path}: ')
        body = quote(f'A fix for the {locked.path} profile at {locked.commit[:7]}; the patch is attached.')
        header += [
            f'Send it yourself: fork the repository ({source}/fork), git apply the patch there and open a pull',
            f'request, or open an issue with the patch attached: {source}/issues/new?title={title}&body={body}',
        ]
    else:
        header.append(f'Send it yourself to the author, as a pull request or an issue with the patch: {source}')
    header += [
        f'Then restore your copy (git restore {profiles.FOLDER}/{profile_id}) and keep the change in config.yaml '
        'meanwhile,',
        'with a comment naming the issue it waits for.',
    ]
    return patch, header


def propose_shaffy(root, checkout, profile_id=None, key=None):
    """A draft Shaffy OS issue in the shape of its Configuration form: a title, the configuration
    (KEY with profiles.explain's reason and a placeholder value, or a placeholder for a mechanism),
    where it should go and why. With PROFILE_ID, the profile's ID, source and commit and the
    checkout's release head it; nothing else of the supplement goes in. Above it: the link that
    opens the form, and what a scan for personal values found, by rule only."""
    reason = None
    if key is not None:
        reason = profiles.PROFILE_EXCLUDED.get(key)
        if reason is None:
            raise ProfileError(
                f'{key} is not a setting profiles are refused; those are: {", ".join(profiles.PROFILE_EXCLUDED)}. '
                'Leave out --key for a mechanism profiles need.'
            )
    configuration = []
    if profile_id is not None:
        locked = _read_lock(Path(root)).get(profile_id)
        if locked is None:
            raise ProfileError(f'{profile_id} is not vendored here; name a vendored profile, or none.')
        configuration.append(
            f'# Profile {profile_id} at {locked.commit[:7]} ({locked.source}), '
            f'Shaffy OS {release(checkout) or "without a release tag"}.'
        )
    if key is None:
        title = 'Profiles: <the mechanism profiles need>'
        configuration.append('# <what a profile cannot share today, and what it would do>')
    else:
        title = f'Profiles: let a profile set {key}'
        configuration += [
            f'# {line}' for line in textwrap.wrap(f'A profile may not set {key} today: {reason}.', width=96)
        ]
        *parents, leaf = key.split('.')
        configuration += [f'{"  " * depth}{parent}:' for depth, parent in enumerate(parents)]
        configuration.append(f'{"  " * len(parents)}{leaf}: <what the profile would set>')
    draft = [
        f'Title: {title}',
        'The configuration:',
        *(f'  {line}' for line in configuration),
        f'Where it should go: {MECHANISM}',
        'Why: <who else it helps, and what it changes for someone who uses the profile>',
    ]
    rules = literals.static_rules() + literals.environment_rules(checkout)
    hits = sorted({rule for line in draft for rule, find in rules if find(line) or find(line.replace('_', '-'))})
    return [
        'A Shaffy OS issue in the shape of its Configuration form. Read it, replace anything personal, then',
        f'open the form and paste each part: {SHAFFY_ISSUES}/new?template={PurePosixPath(FORM).name}',
        'Personal values: '
        + (f'replace what matches {", ".join(hits)} first.' if hits else 'none found in the draft.'),
        '',
        *draft,
    ]


# -- read by check, status and outdated ---------------------------------------------------


def findings(root, shaffy):
    """check's notes about profiles/: an interrupted command's leftovers, vendored profiles
    config.yaml does not list, files and folders the lock does not hold, and keys or keyboard
    matches two listed profiles both set, each naming the command or the authors that settle it."""
    folder = Path(root) / profiles.FOLDER
    if not folder.is_dir():
        return []
    lock, problems = profiles.read_lock(root)
    notes = list(problems)
    listed = [profile['id'] for profile in shaffy['profiles']]
    for leftover in sorted(folder.iterdir()):
        if leftover.name.startswith((STAGING, LOCK_TEMPORARY)):
            notes.append(f'{leftover}: left by an interrupted profile command; the next one deletes it')
    for profile_id, locked in sorted(lock.items()):
        present = folder / profile_id
        if not present.is_dir():
            if profile_id not in listed:
                notes.append(
                    f'{profiles.LOCK} records {profile_id}, which has no folder: ./bin/shaffy profile update '
                    f'{profile_id} restores it, or ./bin/shaffy profile remove {profile_id} drops it'
                )
        elif profile_id not in listed:
            notes.append(
                f'{profiles.FOLDER}/{profile_id} is vendored, but config.yaml does not list it, so nothing reads it: '
                f'list it under profiles:, or ./bin/shaffy profile remove {profile_id}'
            )
        else:
            notes += [
                f'{profiles.FOLDER}/{profile_id}/{name}: not in the lock, so nothing reads it; delete it, or '
                f'./bin/shaffy profile propose {profile_id} drafts it for the author'
                for name in sorted(set(_contents(present)) - set(locked.files))
            ]
    for owner in sorted(path for path in folder.iterdir() if path.is_dir() and not path.name.startswith('.')):
        for present in sorted(owner.iterdir()):
            profile_id = f'{owner.name}/{present.name}'
            if profile_id not in lock:
                notes.append(
                    f'{profiles.FOLDER}/{profile_id} has no lock entry, so nothing reads it: ./bin/shaffy profile '
                    f'remove {profile_id} deletes it'
                )
    return notes + _overlaps(root, shaffy)


def summary(root, shaffy):
    """status's lines: each profile in use with its commit, ref and source, the notes, and one
    invitation when config.yaml itself sets what a profile could share."""
    used = shaffy['profiles']
    lines = [f'Profiles: {len(used)} in use' if used else 'Profiles: none in use']
    for profile in used:
        follows = 'pinned' if profiles.COMMIT.match(profile['ref']) else f'following {profile["ref"]}'
        lines.append(f'  {profile["id"]} at {profile["commit"][:7]}, {follows} of {profile["source"]}')
    lines += [f'  Note: {note}' for note in shaffy['notes'] + findings(root, shaffy)]
    shareable = _shareable(_config(root))
    if shareable:
        lines.append(
            f'  config.yaml also sets {shareable} that a profile could share if they are not personal: '
            'docs/PROFILES.md#share-yours'
        )
    return lines


def behind(root, upstream):
    """(summary, lines) for outdated: each vendored profile whose ref names another commit now,
    with one ls-remote per source and ref; a source that cannot be read hides no other."""
    lock, problems = profiles.read_lock(root)
    if problems:
        return f'not checked: {problems[0]}', []
    if not lock:
        return 'none vendored', []
    lines, late, commits = [], 0, {}
    for profile_id, locked in sorted(lock.items()):
        if profiles.COMMIT.match(locked.ref):
            lines.append(f'{profile_id} pinned at {locked.commit[:7]}')
            continue
        if (locked.source, locked.ref) not in commits:
            try:
                commits[locked.source, locked.ref] = _resolve(upstream, locked.source, locked.ref)
            except ProfileError as error:
                commits[locked.source, locked.ref] = error
        found = commits[locked.source, locked.ref]
        if isinstance(found, ProfileError):
            lines.append(f'{profile_id}: not checked: {found}')
        elif found != locked.commit:
            late += 1
            where = f'{locked.ref} of {locked.source.removeprefix("https://")}'
            lines.append(f'{profile_id} {locked.commit[:7]} → {found[:7]} ({where})')
    if not late:
        return 'up to date', lines
    return (
        f"{late} behind; take each with ./bin/shaffy profile update ID, which shows the author's commits and "
        'what runs code',
        lines,
    )


# -- internal -----------------------------------------------------------------------------


def _patch(path, old, new):
    """A git-apply patch from OLD to NEW ({name: bytes}), both under PATH/ in the author's
    repository; written by git in a private repository that reads no configuration."""
    with tempfile.TemporaryDirectory(prefix='shaffy-propose-') as directory:
        env = dict(os.environ, GIT_DIR=f'{directory}/git', GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')

        def git(*arguments, stdin=None, index=None):
            return subprocess.run(
                ['git', *arguments],
                input=stdin,
                capture_output=True,
                env={**env, **({'GIT_INDEX_FILE': index} if index else {})},
                check=True,
            ).stdout

        git('init', '--quiet', '--bare', f'{directory}/git')
        trees = []
        for side, files in (('old', old), ('new', new)):
            names = sorted(files)
            for number, name in enumerate(names):
                Path(directory, f'{side}-{number}').write_bytes(files[name])
            paths = ''.join(f'{directory}/{side}-{number}\n' for number in range(len(names)))
            blobs = git('hash-object', '-w', '--stdin-paths', stdin=paths.encode()).decode().split()
            index = f'{directory}/{side}.index'
            listing = ''.join(f'100644 {blob}\t{path}/{name}\n' for blob, name in zip(blobs, names, strict=True))
            git('update-index', '--add', '--index-info', stdin=listing.encode(), index=index)
            trees.append(git('write-tree', index=index).decode().strip())
        return git(
            'diff-tree',
            '-r',
            '-p',
            '--binary',
            '--full-index',
            '--no-color',
            '--no-ext-diff',
            '--no-textconv',
            '--src-prefix=a/',
            '--dst-prefix=b/',
            *trees,
        ).decode(errors='surrogateescape')


def _personal(patch, rules):
    """(path, line, rule) for each added line of PATCH a personal-value rule matches."""
    hits, path, number = set(), None, 0
    for line in patch.splitlines():
        if line.startswith('+++ '):
            path = line.removeprefix('+++ b/') if line.startswith('+++ b/') else None
        elif line.startswith('@@'):
            number = int(re.search(r'\+(\d+)', line).group(1)) - 1
        elif line.startswith('+'):
            number += 1
            text = line[1:]
            hits |= {(path, number, rule) for rule, find in rules if find(text) or find(text.replace('_', '-'))}
        elif line.startswith(' '):
            number += 1
    return sorted(hits)


def _scanned(hits, what):
    if not hits:
        return [f'Personal values: none found in {what}.']
    return [f'Personal values: replace these in {what} before you send it (the value is not shown):'] + [
        f'  {path}:{number}: {rule}' for path, number, rule in hits
    ]


def _overlaps(root, shaffy):
    """Notes on what two listed profiles both set where the later one silently wins: a key of
    their Noctalia, Alacritty or mise fragments with different values, and a keyboard match."""
    issues = {profile['id']: profiles.issues(profile['source']) for profile in shaffy['profiles']}
    setters = {}
    for asset in shaffy['assets']:
        if asset.get('fragment') in ('noctalia', 'alacritty', 'mise'):
            try:
                data = tomllib.loads(Path(asset['path']).read_text())
            except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError:
                continue  # the render names a fragment its tool cannot read
            for key, value in _leaves(data):
                setters.setdefault((asset['fragment'], key), []).append((asset['layer'], value))
    for profile in shaffy['profiles']:
        reference = _manifest(Path(root) / profiles.FOLDER / profile['id']).get('overrides', {}).get('keyboard_hwdb')
        if reference:
            text = (Path(root) / profiles.FOLDER / profile['id'] / reference).read_text()
            for match in {line for line in text.splitlines() if line and not line[0].isspace() and line[0] != '#'}:
                setters.setdefault(('keyboard map', match), []).append((profile['id'], text))
    notes = []
    for (tool, key), found in sorted(setters.items()):
        names = list(dict.fromkeys(layer for layer, _ in found))
        if len(names) < 2 or (tool != 'keyboard map' and len({json.dumps(v, default=str) for _, v in found}) < 2):
            continue
        notes.append(
            f'{tool} {key}: {" and ".join(names)} both set it; {names[-1]}, later in the list, wins where they '
            f'differ. Set it in your own {"overrides.keyboard_hwdb" if tool == "keyboard map" else tool} fragment '
            f'to choose, or tell the authors: {", ".join(dict.fromkeys(issues[name] for name in names))}'
        )
    return notes


def _leaves(data, prefix=''):
    for key, value in data.items():
        if isinstance(value, dict):
            yield from _leaves(value, f'{prefix}{key}.')
        else:
            yield f'{prefix}{key}', value


def _shareable(config):
    """What config.yaml itself sets that a profile could share, as one phrase, or ''."""
    found = []
    for section, allowed in profiles.PROFILE_SECTIONS.items():
        value = config.get(section)
        if not isinstance(value, dict) or not value:
            continue
        names = [name for name in (allowed or value) if name in value]
        if section == 'assets':
            found.append(f'{len(value)} asset{"s" * (len(value) != 1)}')
        elif len(names) == 1:
            found.append(f'{section}.{names[0]}')
        elif names:
            found.append(f'{section} ({", ".join(names)})')
    return ', '.join(found[:-1]) + f' and {found[-1]}' if len(found) > 1 else ''.join(found)
