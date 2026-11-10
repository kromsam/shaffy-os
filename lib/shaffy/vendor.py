"""The profile commands: ./bin/shaffy profile add, update and remove (docs/PROFILES.md).

The only code that fetches a profile into a supplement (through pins.Upstream) or writes its
profiles/ folder: the vendored folders and profiles/lock.json. It writes nothing else: never
config.yaml, never Git's index, never a commit, and nothing upstream. sharing.py drafts what goes
upstream and gives check, status and outdated their profile lines; it imports this module, never
the other way. fastload hashes neither, so a change here never invalidates a settings cache.

Every write converges on "the folder and its lock entry match commit C": a command stages the
fetched files in profiles/.staging-*, checks them as a load would, swaps the folder in, and
writes the lock last. A crash at any point leaves a state the next command or load names, and
running the command again finishes it.
"""

import contextlib
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import tempfile
import tomllib
from urllib.parse import urlsplit

from . import loader, pins, profiles
from .selection import SelectionError

LIMITS = (200, 8 * 2**20)  # files and bytes: a profile is small, and a capture copies it every run
SUBJECTS = 30  # the author's commit subjects an update prints before "and N more"
STAGING, LOCK_TEMPORARY = '.staging-', '.lock-'

# Why an entry runs code or reaches the network, for the add and update review.
RUNS = {
    ('overrides', 'niri'): 'Niri starts what it names (spawn, spawn-at-startup)',
    ('overrides', 'noctalia'): 'its launcher entries run commands',
    ('overrides', 'fish'): 'runs in every shell',
    ('overrides', 'alacritty'): 'names the shell and hint commands Alacritty starts',
    ('overrides', 'mise'): 'installs tools and may set hooks and the environment',
    ('overrides', 'keyboard_hwdb'): 'root installs it into the hardware database',
    ('packages', 'extra'): 'root installs it with pacman at the next system apply',
    ('lazyvim', 'extras'): 'loads editor plugins, some outside the managed lock',
    ('ai', 'claude_plugins'): 'the workspace installer installs it from',
}
# Asset targets another program runs, whatever their mode.
RUN_TARGETS = (
    '~/.config/autostart/',
    '~/.config/systemd/user/',
    '~/.config/fish/conf.d/',
    '~/.local/share/applications/',
)
# What stays on the machine when a profile's entry leaves (docs/ARCHITECTURE.md, "Absence and removal").
STAYS = {
    'fragment': 'its file leaves the home at the next apply',
    'keyboard_hwdb': 'its part leaves the keyboard map at the next system apply',
    'assets': 'stays in the home, unmanaged, after the next apply',
    'packages': 'stays installed; list it in packages.remove to uninstall it',
    'lazyvim': 'leaves lazyvim.json; :Lazy clean deletes its plugin files',
    'claude_plugins': 'stays installed and enabled',
    'mcp_servers': 'stays in the AI clients, or the public default returns; enabled: false in config.yaml removes it',
}


class ProfileError(Exception):
    """A profile command cannot go on; nothing was written, or the rerun converges."""


@dataclass(frozen=True)
class Effect:
    """One thing a profile asks of the machine, as the add and update review list it."""

    key: str  # stable identity: 'overrides.fish', 'assets ~/.local/bin/cite', 'packages.extra gimp'
    detail: str  # the profile's file, for a fragment
    runs: str  # why it runs code or reaches the network; '' when it does neither
    stays: str  # what stays on the machine when it leaves
    content: str  # what a change compares: a file's SHA-256 or the value's JSON


@dataclass(frozen=True)
class Review:
    """What add or update did to one profile, ready to print."""

    before: object  # the profiles.Locked it replaced; None for add
    after: object  # the profiles.Locked it wrote
    description: str
    subjects: object  # the author's '%h %s' lines in between, a tuple; None: the history was rewritten
    changes: tuple  # ('+', '~' or '-', Effect), code-running first
    requires: dict  # integration -> its state in config.yaml, or None
    installs: tuple  # new mise tools and Claude plugins, which the workspace installer installs
    listed: bool  # whether config.yaml lists the profile already

    def lines(self):
        """The review as the person reads it, without a control character an author could plant."""
        before, after = self.before, self.after
        where = f'{after.ref} of {after.source}'
        if before is None:
            files = len(after.files)
            lines = [
                f'Vendored {after.id} at {after.commit[:7]} ({where})',
                f'into {profiles.FOLDER}/{after.id}: {files} file{"s" * (files != 1)}.',
                f'  {self.description}',
            ]
        elif before == after and not self.changes:
            return [f'{after.id}: up to date at {after.commit[:7]} ({where}).']
        else:
            changed = len(
                {name for name in {*before.files, *after.files} if before.files.get(name) != after.files.get(name)}
            )
            moved = f'{before.commit[:7]} → ' if before.commit != after.commit else 'at '
            files = f'{changed or "no"} file{"s" * (changed != 1)}'
            lines = [f'{after.id}: {moved}{after.commit[:7]} ({where}), {files} changed.']
            if self.subjects is None:
                lines.append(
                    f'The author rewrote the history since {before.commit[:7]}: read the whole change with '
                    f'git diff -- {profiles.FOLDER}/{after.id}.'
                )
            elif self.subjects:
                lines.append(f"The author's commits to {after.path}/ in between, newest first:")
                lines += [f'  {subject}' for subject in self.subjects[:SUBJECTS]]
                if len(self.subjects) > SUBJECTS:
                    lines.append(f'  and {len(self.subjects) - SUBJECTS} more')
        running = [(mark, effect) for mark, effect in self.changes if _running(mark, effect)]
        other = [(mark, effect) for mark, effect in self.changes if not _running(mark, effect)]
        width = max((len(_label(effect)) for _, effect in self.changes), default=0)
        for title, group in (
            ('Runs code or reaches the network; read these first:', running),
            ('Other entries:' if before is None else 'Other changes:', other),
        ):
            if group:
                lines.append(title)
                lines += [
                    f'  {mark} {_label(effect):<{width}}  {effect.stays if mark == "-" else effect.runs}'.rstrip()
                    for mark, effect in group
                ]
        if self.requires:
            states = ', '.join(_requirement(name, state) for name, state in sorted(self.requires.items()))
            lines.append(f'Requires integrations: {states}.')
        if self.installs:
            lines.append(
                f'New {" and ".join(self.installs)}: the workspace installer installs them; ./bin/shaffy update '
                'runs it, after the next home apply.'
            )
        issues = profiles.issues(after.source)
        if before is None:
            listing = (
                'it is listed in config.yaml already, so ./bin/shaffy check'
                if self.listed
                else f'list "- {after.id}" under\nprofiles: in config.yaml, then ./bin/shaffy check'
            )
            lines += [
                f'Next: read it (git add --intent-to-add {profiles.FOLDER} && git diff -- {profiles.FOLDER}), '
                f'{listing}, diff, commit and apply. Nothing changes before that.',
                f'Something wrong or missing in it? ./bin/shaffy profile propose {after.id} drafts it for the author:',
                f'  {issues}',
                'A need no profile can express is a Shaffy OS change: '
                f'./bin/shaffy profile propose {after.id} --to shaffy',
            ]
        else:
            compare = _compare(after.source, before.commit, after.commit)
            if compare:
                lines.append(f'Compare: {compare}')
            lines += [
                f'Review with git diff -- {profiles.FOLDER} and commit; nothing reaches the machine before an apply.',
                f'Something wrong in it? ./bin/shaffy profile propose {after.id} drafts it for the author: {issues}',
            ]
        return [pins.printable(line) for text in lines for line in text.split('\n')]


@dataclass(frozen=True)
class Outcome:
    """What add or update did: a review per profile, the profiles update kept as they were, and
    what the settings say with the reviewed profiles listed."""

    reviews: tuple
    refused: tuple  # one message per profile update kept as it was
    loaded: tuple  # lines: the problems or the notes of the settings with the profiles listed

    def lines(self):
        lines = []
        for review in self.reviews:
            lines += [*([''] if lines else []), *review.lines()]
        return [*lines, *([''] if lines and self.loaded else []), *self.loaded]


def source_url(text):
    """The https URL and the lower-cased OWNER of OWNER/REPO (GitHub) or an https Git URL."""
    short = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9._-]+)', text)
    if short:
        return f'https://github.com/{text}', short.group(1).lower()
    parts = urlsplit(text)
    path = [part for part in parts.path.split('/') if part]
    if (
        parts.scheme == 'https'
        and parts.hostname
        and not (parts.username or parts.password or parts.query or parts.fragment)
        and len(path) >= 2
    ):
        return text.rstrip('/'), path[0].lower()
    raise ProfileError(
        f'{text!r} is not a profile source: OWNER/REPO on GitHub, or the https URL of a Git repository '
        'with no user name or token in it'
    )


# -- commands: the only code that fetches or writes profiles/ ---------------------------------


def add(root, checkout, source, names, ref, upstream):
    """Vendor NAMES from SOURCE at REF's commit: one fetch for all of them, the lock written last."""
    root = Path(root)
    url, owner = source_url(source)
    wanted = {}
    for name in dict.fromkeys(names):
        try:
            profiles.parse_id(f'{owner}/{name}')
        except ValueError:
            raise ProfileError(
                f'{owner}/{name} cannot be a profile ID: OWNER/NAME in lower-case letters, digits and hyphens, '
                "NAME the profile's folder at the top of the repository"
            ) from None
        wanted[f'{owner}/{name}'] = name
    config = _config(root)
    with _exclusive(root) as folder:
        lock = _read_lock(root)
        for profile_id, name in wanted.items():
            held = lock.get(profile_id)
            if held and (held.source, held.path) != (url, name):
                raise ProfileError(
                    f'{profile_id} is vendored already from {held.path}/ in {held.source}; one ID is one profile. '
                    f'Drop it from config.yaml and run ./bin/shaffy profile remove {profile_id} first.'
                )
            if held:
                raise ProfileError(
                    f'{profile_id} is vendored already, at {held.commit[:7]}; ./bin/shaffy profile update '
                    f'{profile_id} takes a newer commit.'
                )
        commit = _resolve(upstream, url, ref)
        staged = {}
        try:
            for profile_id, name in wanted.items():
                staged[profile_id] = _stage(folder, checkout, profile_id, url, name, commit, upstream)
                present = _place(folder, profile_id)
                if present.exists() and _contents(present) != _contents(staged[profile_id][0]):
                    raise ProfileError(
                        f'{present} is there without a lock entry and differs from {profile_id} at {commit[:7]}; '
                        f'move it out of {profiles.FOLDER}/, then run this again.'
                    )
        except ProfileError:
            for path, _ in staged.values():
                shutil.rmtree(path)
            raise
        reviews = []
        for profile_id, (path, data) in staged.items():
            changes = _changes({}, _effects(path, data))
            installs = _installs({}, path, path, data)
            _swap(folder, profile_id, path)
            lock[profile_id] = profiles.Locked(
                profile_id, url, wanted[profile_id], ref, commit, _contents(folder / profile_id)
            )
            reviews.append(
                Review(
                    None,
                    lock[profile_id],
                    data['description'],
                    (),
                    changes,
                    _requires(data, config),
                    installs,
                    profile_id in _listed(config),
                )
            )
        _write_lock(root, lock)
        loaded = _loaded(root, checkout, config, list(wanted))
    return Outcome(tuple(reviews), (), loaded)


def update(root, checkout, ids, ref, upstream):
    """Re-vendor IDS (every locked one without IDS) at the newest commit of their ref, or of REF,
    which they follow from then on. A profile whose copy has hand edits, or whose new commit does
    not load, is refused and kept as it is; the others go on."""
    root = Path(root)
    config = _config(root)
    with _exclusive(root) as folder:
        lock = _read_lock(root)
        if not lock:
            raise ProfileError('nothing is vendored here; ./bin/shaffy profile add OWNER/REPO NAME vendors a profile.')
        unknown = [profile_id for profile_id in ids if profile_id not in lock]
        if unknown:
            raise ProfileError(
                f'{", ".join(unknown)} {"is" if len(unknown) == 1 else "are"} not vendored here; '
                './bin/shaffy profile add OWNER/REPO NAME vendors a profile.'
            )
        written = dict(lock)
        reviews, refused, commits = [], [], {}
        for profile_id in dict.fromkeys(ids) or sorted(lock):
            locked = lock[profile_id]
            follows = ref or locked.ref
            try:
                if (locked.source, follows) not in commits:
                    commits[locked.source, follows] = _resolve(upstream, locked.source, follows)
                commit = commits[locked.source, follows]
                review = _update_one(folder, checkout, locked, follows, commit, config, upstream)
            except ProfileError as error:
                refused.append(str(error))
                continue
            reviews.append(review)
            written[profile_id] = review.after
        if written != lock:
            _write_lock(root, written)
        changed = [review.after.id for review in reviews if review.before != review.after]
        loaded = _loaded(root, checkout, config, changed) if changed else ()
    return Outcome(tuple(reviews), tuple(refused), loaded)


def _update_one(folder, checkout, locked, follows, commit, config, upstream):
    """Swap one profile's folder to COMMIT; returns its Review. The caller writes the lock."""
    present = _place(folder, locked.id)
    current = _contents(present) if present.is_dir() else None
    if commit == locked.commit and current == locked.files:
        after = profiles.Locked(locked.id, locked.source, locked.path, follows, commit, locked.files)
        return Review(locked, after, '', (), (), {}, (), True)  # up to date, or following another ref
    subjects = () if commit == locked.commit else upstream.subjects(locked.source, locked.commit, commit, locked.path)
    staged, data = _stage(folder, checkout, locked.id, locked.source, locked.path, commit, upstream, kept=True)
    new = _contents(staged)
    if current is not None and current not in (locked.files, new):
        shutil.rmtree(staged)
        raise ProfileError(
            f'{locked.id}: its copy has hand edits, so update keeps it as it is. Propose them first: '
            f'./bin/shaffy profile propose {locked.id} drafts them for the author; then git restore '
            f'{profiles.FOLDER}/{locked.id} and run this again.'
        )
    # The entries before: the copy as locked, which is the staged one when the commit is the same;
    # unknown after a crash left the folder missing or already swapped, so every entry shows as new.
    if current == locked.files:
        old_folder, old_data = present, _manifest(present)
    elif commit == locked.commit:
        old_folder, old_data = staged, data
    else:
        old_folder, old_data = staged, {}
    changes = _changes(_effects(old_folder, old_data), _effects(staged, data))
    installs = _installs(old_data, old_folder, staged, data)
    _swap(folder, locked.id, staged)
    return Review(
        locked,
        profiles.Locked(locked.id, locked.source, locked.path, follows, commit, new),
        data['description'],
        tuple(subjects) if subjects is not None else None,
        changes,
        _requires(data, config),
        installs,
        locked.id in _listed(config),
    )


def remove(root, ids):
    """Delete IDS' folders and lock entries; returns what stays on the machine and each author's
    issues link. Refuses an ID config.yaml still lists."""
    root = Path(root)
    for profile_id in ids:
        try:
            profiles.parse_id(profile_id)  # it names a folder this deletes
        except ValueError as error:
            raise ProfileError(str(error)) from None
    listed = [profile_id for profile_id in ids if profile_id in _listed(_config(root, strict=True))]
    if listed:
        raise ProfileError(
            f'config.yaml still lists {", ".join(listed)}; drop {"it" if len(listed) == 1 else "them"} from '
            'profiles: first, so the settings stop using what this deletes.'
        )
    lines = []
    with _exclusive(root) as folder:
        lock = _read_lock(root)
        for profile_id in dict.fromkeys(ids):
            present = _place(folder, profile_id)
            if profile_id not in lock and not present.exists():
                lines.append(f'{profile_id}: not vendored here; nothing to remove.')
                continue
            lines += [
                f'Removed {profiles.FOLDER}/{profile_id} and its lock entry; review with git diff and commit.',
                'At the next apply its settings stop applying.',
                *_staying(_effects(present, _manifest(present))),
            ]
            if profile_id in lock:
                lines += [
                    'Removed it because something was wrong or missing? Tell the author:',
                    f'  {profiles.issues(lock[profile_id].source)}',
                ]
        remaining = {profile_id: entry for profile_id, entry in lock.items() if profile_id not in ids}
        if remaining != lock:
            _write_lock(root, remaining)  # first: a crash then leaves a folder that check names
        for profile_id in ids:
            present = _place(folder, profile_id)
            if present.exists():
                shutil.rmtree(present)
            with contextlib.suppress(OSError):
                present.parent.rmdir()  # its owner's folder, once empty
    return lines


def install(root, profile_id, files, locked):
    """Make profiles/OWNER/NAME hold exactly FILES and record LOCKED; profile check's temporary
    supplements and the tests' fixtures are built with this."""
    owner, name = profiles.parse_id(profile_id)
    folder = Path(root) / profiles.FOLDER / owner / name
    if folder.exists():
        shutil.rmtree(folder)
    for relative, content in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    lock, problems = profiles.read_lock(root)
    if problems:
        raise ProfileError('\n'.join(problems))
    lock[profile_id] = locked
    _write_lock(root, lock)


# -- internal -----------------------------------------------------------------------------


@contextlib.contextmanager
def _exclusive(root):
    """The profiles folder, locked against another profile command (flock on the folder itself,
    so no lock file appears in Git), with an earlier crash's leftovers deleted. A folder left
    empty is removed again."""
    folder = Path(root) / profiles.FOLDER
    if folder.is_symlink():
        raise ProfileError(f'{folder} is a link; the profile commands write only into a folder of the supplement')
    folder.mkdir(exist_ok=True)
    handle = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    held = False
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProfileError(f'another profile command is changing {folder}; run this one when it is done') from None
        held = True
        for leftover in folder.iterdir():
            if leftover.name.startswith(STAGING):
                shutil.rmtree(leftover)
            elif leftover.name.startswith(LOCK_TEMPORARY):
                leftover.unlink()
        yield folder
    finally:
        if held:
            with contextlib.suppress(OSError):
                folder.rmdir()  # only when empty
        os.close(handle)


def _place(folder, profile_id):
    """FOLDER/OWNER/NAME, refused when a link would lead a write or a deletion out of profiles/."""
    present = folder / profile_id
    for path in (present.parent, present):
        if path.is_symlink():
            raise ProfileError(f'{path} is a link; {profiles.FOLDER}/ holds plain folders. Move it aside.')
    return present


def _read_lock(root):
    lock, problems = profiles.read_lock(root)
    if problems:
        raise ProfileError('\n'.join(problems))
    return lock


def _config(root, strict=False):
    """config.yaml's settings; with STRICT a file that cannot be read is an error, else empty."""
    try:
        return loader.parse(Path(root) / 'config.yaml')
    except loader.SettingsError as error:
        if strict:
            raise ProfileError(f'config.yaml cannot be read, so its profiles list is unknown: {error}') from None
        return {}


def _listed(config):
    listed = config.get('profiles')
    return list(listed) if isinstance(listed, list) else []


def _resolve(upstream, url, ref):
    """The commit REF names in URL: HEAD, a branch, a tag, or a commit itself (pinned)."""
    if profiles.COMMIT.match(ref):
        return ref
    try:
        refs = upstream.refs(url, ref)
    except (subprocess.SubprocessError, OSError) as error:  # unreachable, missing, or not a repository
        reason = (getattr(error, 'stderr', None) or str(error)).strip().splitlines()
        raise ProfileError(f'{url} cannot be read: {pins.printable(reason[-1]) if reason else error}') from None
    for name in ('HEAD',) if ref == 'HEAD' else (f'refs/heads/{ref}', f'refs/tags/{ref}'):
        if name in refs:
            return refs[name]
    raise ProfileError(f'{url} has no branch or tag {ref}')


def _stage(folder, checkout, profile_id, url, path, commit, upstream, kept=False):
    """(staged folder, its profile.yaml) for PATH at COMMIT: the fetched files written to
    profiles/.staging-…, checked as a load checks a listed profile, then pruned to profile.yaml,
    the files it names and README.md. KEPT says that a refusal keeps the current copy."""
    issues = profiles.issues(url)
    try:
        tree = upstream.tree(url, commit, path, LIMITS)
    except ValueError as error:
        raise ProfileError(f'{profile_id}: {error}. Tell the author: {issues}') from None
    if profiles.MANIFEST not in tree:
        raise ProfileError(f'{profile_id}: {path}/ in {url} at {commit[:7]} has no {profiles.MANIFEST}')
    owner, name = profiles.parse_id(profile_id)
    staged = Path(tempfile.mkdtemp(prefix=f'{STAGING}{owner}-{name}-', dir=folder))
    for relative, content in tree.items():
        (staged / relative).parent.mkdir(parents=True, exist_ok=True)
        (staged / relative).write_bytes(content)
    problems = loader.check_profile(checkout, staged, f'{path}/{profiles.MANIFEST} at {commit[:7]}', profile_id, issues)
    if problems:
        shutil.rmtree(staged)
        stays = f' {profile_id} stays as it is.' if kept else ''
        raise ProfileError(
            f'{profile_id} at {commit[:7]} does not load here.{stays} Tell the author: {issues}\n  '
            + '\n  '.join(problem.replace(f'{staged}/', f'{path}/') for problem in problems)
        )
    data = loader.parse(staged / profiles.MANIFEST)
    named = {profiles.MANIFEST, profiles.README, *_references(data)}
    for relative in tree:
        if relative not in named:
            (staged / relative).unlink()
    for directory, _, _ in sorted(os.walk(staged), reverse=True):
        with contextlib.suppress(OSError):
            os.rmdir(directory)  # only the ones the pruning emptied
    return staged, data


def _references(data):
    """The files a profile.yaml names, relative to its folder."""
    named = list(data.get('overrides', {}).values())
    named += [value['file'] if isinstance(value, dict) else value for value in data.get('assets', {}).values()]
    return [os.path.normpath(reference) for reference in named]


def _swap(folder, profile_id, staged):
    """Old folder aside, staged folder in, old folder deleted: never a mix of the two."""
    present = _place(folder, profile_id)
    aside = None
    if present.exists():
        aside = Path(tempfile.mkdtemp(prefix=f'{STAGING}{profile_id.replace("/", "-")}-old-', dir=folder))
        os.rename(present, aside / 'old')
    present.parent.mkdir(exist_ok=True)
    os.rename(staged, present)
    if aside:
        shutil.rmtree(aside)


def _write_lock(root, lock):
    """Sorted, indented JSON with a final newline, through a temporary file and os.replace; no
    file once nothing is vendored."""
    path = Path(root) / profiles.LOCK
    if not lock:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        'lock_version': profiles.LOCK_VERSION,
        'profiles': {
            profile_id: {
                'commit': entry.commit,
                'files': dict(sorted(entry.files.items())),
                'path': entry.path,
                'ref': entry.ref,
                'source': entry.source,
            }
            for profile_id, entry in sorted(lock.items())
        },
    }
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=LOCK_TEMPORARY)
    with os.fdopen(handle, 'w') as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write('\n')
    os.replace(temporary, path)


def _contents(folder):
    """{path: SHA-256} of every file in FOLDER; a link counts as a file that differs from any."""
    found = {}
    for directory, _, names in os.walk(folder):
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(folder).as_posix()
            found[relative] = 'link' if path.is_symlink() else _digest(path)
    return found


def _digest(path):
    try:
        with open(path, 'rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    except OSError:
        return ''


def _manifest(folder):
    """A vendored copy's profile.yaml, or {} when it cannot be read."""
    try:
        return loader.parse(Path(folder) / profiles.MANIFEST)
    except loader.SettingsError:
        return {}


def _effects(folder, data):
    """{key: Effect} for every entry of a profile's settings, classified by RUNS and RUN_TARGETS."""
    folder = Path(folder)
    effects = []
    for tool, reference in data.get('overrides', {}).items():
        stays = STAYS['keyboard_hwdb' if tool == 'keyboard_hwdb' else 'fragment']
        effects.append(
            Effect(f'overrides.{tool}', reference, RUNS[('overrides', tool)], stays, _digest(folder / reference))
        )
    for target, value in data.get('assets', {}).items():
        flags = value if isinstance(value, dict) else {'file': value}
        if flags.get('executable'):
            runs = 'an installed program'
        elif target.startswith(RUN_TARGETS):
            runs = 'another program runs it'
        elif PurePosixPath(target).name in ('mise.toml', '.mise.toml'):
            runs = 'mise reads it: tools, hooks and the environment'
        else:
            runs = ''
        content = json.dumps(
            [_digest(folder / flags['file']), bool(flags.get('executable')), bool(flags.get('private'))]
        )
        effects.append(Effect(f'assets {target}', '', runs, STAYS['assets'], content))
    for name in data.get('packages', {}).get('extra', []):
        effects.append(Effect(f'packages.extra {name}', '', RUNS[('packages', 'extra')], STAYS['packages'], ''))
    for extra in data.get('lazyvim', {}).get('extras', []):
        effects.append(Effect(f'lazyvim.extras {extra}', '', RUNS[('lazyvim', 'extras')], STAYS['lazyvim'], ''))
    for plugin, repository in data.get('ai', {}).get('claude_plugins', {}).items():
        runs = f'{RUNS[("ai", "claude_plugins")]} {repository}'
        effects.append(Effect(f'ai.claude_plugins {plugin}', '', runs, STAYS['claude_plugins'], repository))
    for name, server in data.get('ai', {}).get('mcp_servers', {}).items():
        if server.get('enabled') is False:
            runs = ''
        elif 'command' in server:
            runs = 'the AI clients start: ' + shlex.join([server['command'], *server.get('args', [])])
        elif 'url' in server:
            runs = f'the AI clients send their requests to {server["url"]}'
        else:
            runs = 'changes the public default the AI clients start'
        content = json.dumps(server, sort_keys=True)
        effects.append(Effect(f'ai.mcp_servers.{name}', '', runs, STAYS['mcp_servers'], content))
    return {effect.key: effect for effect in effects}


def _label(effect):
    return f'{effect.key} {effect.detail}'.rstrip()


def _running(mark, effect):
    """Whether a change belongs in the review's first group: an entry that runs code and stays."""
    return bool(effect.runs) and mark != '-'


def _changes(old, new):
    """('+', '~' or '-', Effect) from OLD to NEW: code-running entries first, each group added, then
    changed, then removed."""
    changes = [('+', effect) for key, effect in new.items() if key not in old]
    changes += [('~', effect) for key, effect in new.items() if key in old and old[key] != effect]
    changes += [('-', effect) for key, effect in old.items() if key not in new]
    return tuple(sorted(changes, key=lambda change: not _running(*change)))


def _requirement(name, state):
    """One integration a profile requires, with what config.yaml must say."""
    if state == 'managed':
        return f'{name} (managed in config.yaml)'
    if state is None:
        return f'{name} (not set: add integrations.{name}: managed)'
    return f'{name} ({state} in config.yaml: set integrations.{name}: managed)'


def _requires(data, config):
    states = config.get('integrations') if isinstance(config.get('integrations'), dict) else {}
    return {name: states.get(name) for name in data.get('requires', [])}


def _installs(old_data, old_folder, new_folder, data):
    """The mise tools and Claude plugins DATA adds over OLD_DATA: the workspace installer
    installs them, at ./bin/shaffy update, and nothing else does."""
    tools = [tool for tool in _tools(new_folder, data) if tool not in _tools(old_folder, old_data)]
    old_plugins = old_data.get('ai', {}).get('claude_plugins', {})
    plugins = [plugin for plugin in data.get('ai', {}).get('claude_plugins', {}) if plugin not in old_plugins]
    return tuple(
        f'{what} ({", ".join(names)})' for what, names in (('mise tools', tools), ('Claude plugins', plugins)) if names
    )


def _tools(folder, data):
    reference = data.get('overrides', {}).get('mise')
    if not reference:
        return []
    try:
        return list(tomllib.loads((Path(folder) / reference).read_text()).get('tools', {}))
    except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError:
        return []  # check's render names a fragment mise cannot read


def _staying(effects):
    """remove's lines on what stays on the machine, by section; a fragment leaves with the profile."""
    groups = {}
    for effect in effects.values():
        if effect.key.startswith('overrides.'):
            continue
        section, _, name = effect.key.replace('ai.mcp_servers.', 'ai.mcp_servers ', 1).partition(' ')
        groups.setdefault((section, effect.stays), []).append(name)
    if not groups:
        return []
    return ['These stay on the machine (docs/PROFILES.md#removing-a-profile):'] + [
        f'  {section}: {", ".join(names)} {stays}' for (section, stays), names in groups.items()
    ]


def _compare(source, old, new):
    source = source.rstrip('/').removesuffix('.git')
    return f'{source}/compare/{old[:7]}...{new[:7]}' if source.startswith('https://github.com/') else ''


def _loaded(root, checkout, config, ids):
    """What loading the settings says with IDS listed after config.yaml's own profiles, as check
    will once the person lists them: its problems, or its notes. Loaded once per command."""
    listed = _listed(config)
    listed += [profile_id for profile_id in ids if profile_id not in listed]
    which = f'With {", ".join(ids)} listed in config.yaml, the settings'
    try:
        data = loader.load(checkout, supplement=root, listed=listed)
    except (SelectionError, loader.SettingsError) as error:
        problems = getattr(error, 'problems', [str(error)])
        return (f'{which} do not load yet:', *(f'  {problem}' for problem in problems))
    notes = data['shaffy']['notes']
    if not notes:
        return (f'{which} load.',)
    return (f'{which} load, with these notes:', *(f'  {note}' for note in notes))
