"""The versions this repository pins, where newer ones come from, and how to raise them.

`./bin/shaffy outdated` reports them; scripts/refresh-pins.py raises them on dev, in one
diff to review before a release. Like mise's `latest`, a release counts only once it is
three days old where the upstream records a date, as Galaxy does; git tags and the CI
image's tag carry none. yabridge is a reviewed source: it is reported, and its revision
changes by hand. ruff's target follows the system Python. Tools mise installs follow
`latest` and have no pin here.

Upstream is every read from outside the checkout, so the tests can replace it; the profile
commands (lib/shaffy/vendor.py) read a profile's repository through it too.
"""

from collections.abc import Callable
from dataclasses import dataclass
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.request

MINIMUM_AGE = datetime.timedelta(days=3)
# A plugin lock older than this is reported; plugins move continuously.
LOCK_AGE = datetime.timedelta(days=30)
GALAXY = 'https://galaxy.ansible.com/api/v3/plugin/ansible/content/published/collections/index'
STABLE = re.compile(r'[0-9]+(?:\.[0-9]+)*')


@dataclass
class Pin:
    name: str
    current: str
    latest: str | None = None
    # Rewrites the files for `latest` and returns their paths; None: changed by hand.
    raise_to: Callable[[], list] | None = None
    note: str = ''
    error: str = ''

    @property
    def behind(self):
        return not self.error and self.latest is not None and self.latest != self.current


def printable(text):
    """TEXT without control and format characters, so text an upstream author wrote, such as a
    commit subject, cannot drive the terminal or reorder what it shows."""
    return ''.join(character for character in text if unicodedata.category(character) not in ('Cc', 'Cf'))


class Upstream:
    """The reads from outside the checkout, in one place so the tests can replace them.

    A profile's repository is fetched once per command into a private temporary bare repository,
    which close() deletes; use the instance as a context manager.
    """

    def __init__(self, now=None):
        self.now = now or datetime.datetime.now(datetime.UTC)
        self._scratch = None
        self._fetched = {}  # url -> (bare repository, whether it holds the history)

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()

    def close(self):
        if self._scratch is not None:
            self._scratch.cleanup()
            self._scratch = None
            self._fetched.clear()

    def read(self, url):
        request = urllib.request.Request(url, headers={'User-Agent': 'shaffy-os-pins'})
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()

    def json(self, url):
        return json.loads(self.read(url))

    def refs(self, url, *patterns):
        """{ref: commit} from `git ls-remote`, with annotated tags peeled."""
        result = subprocess.run(
            ['git', 'ls-remote', url, *patterns],
            capture_output=True,
            text=True,
            env=dict(os.environ, GIT_TERMINAL_PROMPT='0'),  # a public repository; a wrong name fails, never prompts
            timeout=60,
            check=True,
        )
        refs = {}
        for line in result.stdout.splitlines():
            commit, ref = line.split('\t')
            peeled = ref.endswith('^{}')
            ref = ref.removesuffix('^{}')
            if peeled or ref not in refs:
                refs[ref] = commit
        return refs

    def python(self):
        """The system Python as a ruff target, such as py314: the newest the distribution ships."""
        result = subprocess.run(
            ['/usr/bin/python3', '-c', 'import sys; print(f"py{sys.version_info[0]}{sys.version_info[1]}")'],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def old_enough(self, stamp):
        when = datetime.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        return self.now - when >= MINIMUM_AGE

    def tree(self, url, commit, folder, limits):
        """{path: bytes} of every file under FOLDER at COMMIT, paths relative to FOLDER.

        Raises ValueError for a missing folder, a link, a submodule, a name with a control or
        format character, a name no checkout could write safely ('..', or one Git reads, such as
        .git or .gitattributes), or more files than LIMITS (files, bytes) allows, all before a
        blob is fetched; then for more bytes than LIMITS allows, before a blob is read. Only the
        folder's blobs are fetched.
        """
        repository = self._repository(url, commit, history=False)
        listing = _git(repository, 'ls-tree', '-r', '-z', '--full-tree', commit, '--', f'{folder}/')
        where = f'{folder}/ in {url} at {commit[:7]}'
        entries = []
        for record in listing.stdout.split(b'\0')[:-1]:
            info, _, raw = record.partition(b'\t')
            mode, kind, blob = info.decode().split()
            try:
                name = raw.decode().removeprefix(f'{folder}/')
            except UnicodeDecodeError:
                raise ValueError(f'{where}: a file name is not UTF-8') from None
            if printable(name) != name:  # before any message shows the name as it is
                raise ValueError(f'{where}: {name!r} holds a control or format character, which no name may')
            if mode == '120000' or kind == 'commit':
                what = 'a link' if kind == 'blob' else 'a submodule'
                raise ValueError(f'{where}: {name} is {what}, and a profile holds plain files only')
            if any(part in ('', '.', '..') or part.lower().startswith('.git') for part in name.split('/')):
                raise ValueError(f'{where}: {name!r} is not a name a profile may hold')
            entries.append((name, blob))
        if not entries:
            raise ValueError(f'{where}: no such folder')
        most_files, most_bytes = limits
        if len(entries) > most_files:
            raise ValueError(f'{where}: {len(entries)} files, more than the {most_files} a profile may hold')
        blobs = b''.join(blob.encode() + b'\n' for _, blob in entries)
        found = _git(repository, 'cat-file', '--batch-check', stdin=blobs).stdout.splitlines()
        missing = b''.join(line.split()[0] + b'\n' for line in found if line.endswith(b' missing'))
        if missing:  # a host that filters sent none of the blobs
            _git(
                repository, 'fetch', '--quiet', '--no-tags', '--no-write-fetch-head', '--stdin', 'origin', stdin=missing
            )
            found = _git(repository, 'cat-file', '--batch-check', stdin=blobs).stdout.splitlines()
        sizes = [int(line.split()[2]) for line in found]
        if sum(sizes) > most_bytes:
            raise ValueError(f'{where}: {sum(sizes)} bytes, more than the {most_bytes} a profile may hold')
        batch = _git(repository, 'cat-file', '--batch', stdin=blobs)
        files, data = {}, batch.stdout
        for (name, _), size in zip(entries, sizes, strict=True):
            _, _, data = data.partition(b'\n')  # "<blob> blob <size>", then the bytes and a newline
            files[name], data = data[:size], data[size + 1 :]
        return files

    def subjects(self, url, old, new, folder):
        """'%h %s' of every commit in OLD..NEW that touched FOLDER, newest first, each without
        control characters; None when OLD is no longer an ancestor of NEW (the author rewrote
        the history)."""
        repository = self._repository(url, new, history=True)
        if _git(repository, 'merge-base', '--is-ancestor', old, new, check=False).returncode:
            return None
        log = _git(
            repository,
            '-c',
            'log.showSignature=false',
            'log',
            '--no-color',
            '--format=%h %s',
            f'{old}..{new}',
            '--',
            f'{folder}/',
        )
        return [printable(line) for line in log.stdout.decode(errors='replace').splitlines()]

    def _repository(self, url, commit, history):
        """A private bare repository that holds COMMIT, one per URL for this instance's lifetime.

        git clone --revision COMMIT, with --depth 1 unless HISTORY. A host that filters (public
        hosts do) sends commits and trees only; tree() fetches the blobs of one folder. When the
        host refuses a commit by its hash (some do unless a branch or tag names it): a clone of
        every branch, which must then hold COMMIT, or ValueError naming the source and the commit.
        Every object git receives is checked (transfer.fsckObjects).
        """
        held = self._fetched.get(url)
        if held and (held[1] or not history) and _holds(held[0], commit):
            return held[0]
        repository = self._new()
        clone = ('clone', '--quiet', '--bare', '--filter=blob:none', '--config', 'transfer.fsckObjects=true')
        depth = () if history else ('--depth', '1')
        cloned = _git(None, *clone, *depth, f'--revision={commit}', '--', url, repository, check=False, timeout=600)
        if cloned.returncode or not _holds(repository, commit):
            shutil.rmtree(repository, ignore_errors=True)
            _git(None, *clone, '--', url, repository, timeout=600)
            if not _holds(repository, commit):
                raise ValueError(f'{url}: no branch or tag holds the commit {commit}')
            history = True
        self._fetched[url] = (repository, history)
        return repository

    def _new(self):
        """A fresh path in this instance's private folder."""
        if self._scratch is None:
            self._scratch = tempfile.TemporaryDirectory(prefix='shaffy-profiles-')
        return Path(self._scratch.name) / str(len(list(Path(self._scratch.name).iterdir())))


def _git(repository, *arguments, stdin=None, check=True, timeout=60):
    """git on REPOSITORY, never asking for a password: a profile's repository is public. A failure
    raises ValueError with git's own reason."""
    command = ['git', *(['--git-dir', str(repository)] if repository else []), *map(str, arguments)]
    try:
        result = subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            # Every fetch is one the code asks for: a missing object is never fetched on demand.
            env=dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1'),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError(f'git {arguments[0]} took longer than {timeout} seconds') from None
    if check and result.returncode:
        reason = printable(result.stderr.decode(errors='replace').strip().replace('\n', ' ')) or result.returncode
        raise ValueError(f'git {arguments[0]} failed: {reason}')
    return result


def _holds(repository, commit):
    return not _git(repository, 'cat-file', '-e', f'{commit}^{{commit}}', check=False).returncode


def numeric(version):
    return tuple(int(part) for part in re.findall(r'[0-9]+', version))


def _edit(root, path, old, new, count=None):
    """Replace `old` with `new` in one file, `count` times when given; returns the path."""
    target = root / path
    text = target.read_text()
    found = text.count(old)
    if found == 0 or (count is not None and found != count):
        raise ValueError(f'{path}: expected {count or "some"} occurrence(s) of {old!r}, found {found}')
    target.write_text(text.replace(old, new))
    return path


def collections(root, upstream):
    text = (root / 'requirements.yml').read_text()
    pins = []
    for name, version in re.findall(r'- name: (\S+)\n\s+version: (\S+)', text):
        pin = Pin(f'Ansible collection {name}', version)
        namespace, collection = name.split('.')
        data = upstream.json(f'{GALAXY}/{namespace}/{collection}/versions/?limit=100&ordering=-version')
        versions = [
            v['version']
            for v in data['data']
            if STABLE.fullmatch(v['version']) and upstream.old_enough(v['created_at'])
        ]
        pin.latest = max(versions, key=numeric, default=version)

        def raise_to(name=name, old=version, pin=pin):
            path = root / 'requirements.yml'
            block = re.compile(rf'(- name: {re.escape(name)}\n\s+version: ){re.escape(old)}\n')
            updated, found = block.subn(lambda match: match.group(1) + pin.latest + '\n', path.read_text())
            if found != 1:
                raise ValueError(f'requirements.yml: no single entry for {name} {old}')
            path.write_text(updated)
            return ['requirements.yml']

        pin.raise_to = raise_to
        pins.append(pin)
    return pins


def yaru(root, upstream):
    script = 'scripts/install-yaru-icons.py'
    text = (root / script).read_text()
    current = re.search(r"VERSION = '([^']+)'", text).group(1)
    digest = re.search(r"SHA256 = '([0-9a-f]{64})'", text).group(1)
    pin = Pin('Yaru icons', current)
    # Keep the release line in use: Ubuntu's `…ubuntu` tags or upstream's plain ones.
    flavour = re.compile(r'[0-9]+(?:\.[0-9]+)+' + re.escape(re.sub(r'^[0-9.]+', '', current)))
    tags = [
        ref.removeprefix('refs/tags/') for ref in upstream.refs('https://github.com/ubuntu/yaru.git', 'refs/tags/*')
    ]
    pin.latest = max((t for t in tags if flavour.fullmatch(t)), key=numeric, default=current)

    def raise_to():
        archive = upstream.read(f'https://github.com/ubuntu/yaru/archive/refs/tags/{pin.latest}.tar.gz')
        sha256 = hashlib.sha256(archive).hexdigest()
        changed = [
            _edit(root, path, current, pin.latest) for path in (script, 'README.md', 'docs/DESKTOP-BEHAVIOUR.md')
        ]
        _edit(root, script, digest, sha256, 1)
        _edit(root, 'docs/DESKTOP-BEHAVIOUR.md', digest, sha256, 1)
        return changed

    pin.raise_to = raise_to
    return [pin]


def better_bibtex(root, upstream):
    script = 'scripts/install-better-bibtex.py'
    text = (root / script).read_text()
    current = re.search(r"VERSION = '([^']+)'", text).group(1)
    digest = re.search(r"SHA256 = '([0-9a-f]{64})'", text).group(1)
    url = re.search(r"URL = f'([^']+)'", text).group(1)
    pin = Pin('Better BibTeX for Zotero', current)
    releases = upstream.json('https://api.github.com/repos/retorquere/zotero-better-bibtex/releases?per_page=30')
    versions = [
        r['tag_name'][1:]
        for r in releases
        if r['tag_name'].startswith('v')
        and STABLE.fullmatch(r['tag_name'][1:])
        and not r['draft']
        and not r['prerelease']
        and upstream.old_enough(r['published_at'])
    ]
    pin.latest = max(versions, key=numeric, default=current)

    def raise_to():
        sha256 = hashlib.sha256(upstream.read(url.replace('{VERSION}', pin.latest))).hexdigest()
        changed = [_edit(root, script, f"VERSION = '{current}'", f"VERSION = '{pin.latest}'", 1)]
        _edit(root, script, digest, sha256, 1)
        return changed

    pin.raise_to = raise_to
    return [pin]


def yabridge(root, upstream):
    text = (root / 'scripts/build-audio-bridge.sh').read_text()
    current = re.search(r'revision=([0-9a-f]{40})', text).group(1)
    branch = re.search(r'--branch (\S+)', text).group(1)
    head = upstream.refs('https://github.com/robbert-vdh/yabridge.git', f'refs/heads/{branch}')
    latest = head.get(f'refs/heads/{branch}', current)
    return [
        Pin(
            f'yabridge ({branch})',
            current[:12],
            latest[:12],
            note='a reviewed source: read the upstream changes, then set the revision in '
            'scripts/build-audio-bridge.sh by hand',
        )
    ]


# The workflows that run in the CI container: this repository's, and the ones supplements and
# profile repositories call.
WORKFLOWS = ('.github/workflows/selftest.yml', '.github/workflows/supplement.yml', '.github/workflows/profiles.yml')


def ci_container(root, upstream):
    workflow = WORKFLOWS[0]
    current = re.search(r'cachyos/cachyos@(sha256:[0-9a-f]{64})', (root / workflow).read_text()).group(1)
    tag = upstream.json('https://hub.docker.com/v2/repositories/cachyos/cachyos/tags/latest')
    pin = Pin('CI container cachyos/cachyos:latest', current[7:19], tag['digest'][7:19])
    pin.raise_to = lambda: [
        *(_edit(root, path, current, tag['digest'], 1) for path in WORKFLOWS),
        _edit(root, 'tests/tool-versions.json', current, tag['digest'], 1),
    ]
    return [pin]


def actions_checkout(root, upstream):
    workflow = WORKFLOWS[0]
    commit, current = re.search(r'actions/checkout@([0-9a-f]{40}) # (v[0-9.]+)', (root / workflow).read_text()).groups()
    refs = upstream.refs('https://github.com/actions/checkout.git', 'refs/tags/v*')
    releases = {
        ref.removeprefix('refs/tags/'): sha
        for ref, sha in refs.items()
        if re.fullmatch(r'refs/tags/v[0-9]+\.[0-9]+\.[0-9]+', ref)
    }
    pin = Pin('GitHub action actions/checkout', current, max(releases, key=numeric, default=current))
    pin.raise_to = lambda: [
        _edit(
            root,
            path,
            f'actions/checkout@{commit} # {current}',
            f'actions/checkout@{releases[pin.latest]} # {pin.latest}',
        )
        for path in WORKFLOWS
    ]
    return [pin]


def lazyvim(root, upstream, run=subprocess.run):
    lock = 'home/dot_config/nvim/lazy-lock.json'
    stamp = subprocess.run(
        ['git', '-C', str(root), 'log', '-1', '--format=%cI', '--', lock], capture_output=True, text=True, check=False
    ).stdout.strip()
    refreshed = datetime.datetime.fromisoformat(stamp) if stamp else upstream.now
    pin = Pin(
        'LazyVim plugin lock',
        f'refreshed {refreshed.date()}',
        note='plugins move continuously; refresh-pins updates them in a sandbox',
    )
    pin.latest = 'due for a refresh' if upstream.now - refreshed > LOCK_AGE else pin.current
    pin.raise_to = lambda: refresh_lazyvim(root, run)
    return [pin]


def render_public(root, template, sandbox):
    """A home template rendered with the public defaults alone, so the lock covers only the
    public extras: the sandbox's HOME binds no supplement."""
    config = sandbox / 'chezmoi.toml'
    config.write_text(f'persistentState = "{sandbox / "chezmoistate.boltdb"}"\n')
    return subprocess.run(
        ['chezmoi', '--config', str(config), '--source', str(root / 'home'), 'execute-template'],
        input=template.read_text(),
        capture_output=True,
        text=True,
        env={'PATH': os.environ['PATH'], 'HOME': str(sandbox), 'LANG': 'C.UTF-8'},
        check=True,
    ).stdout


def refresh_lazyvim(root, run=subprocess.run):
    """Update every plugin in a throwaway Neovim home and keep the lock it writes."""
    lock = root / 'home/dot_config/nvim/lazy-lock.json'
    with tempfile.TemporaryDirectory(prefix='shaffy-lazyvim-') as directory:
        sandbox = Path(directory)
        config = sandbox / 'config/nvim'
        for source in (root / 'home/dot_config/nvim').rglob('*'):
            if source.is_file():
                relative = Path(
                    *(
                        part.replace('dot_', '.', 1) if part.startswith('dot_') else part
                        for part in source.relative_to(root / 'home/dot_config/nvim').parts
                    )
                )
                (config / relative).parent.mkdir(parents=True, exist_ok=True)
                if source.suffix == '.tmpl':
                    (config / relative).with_suffix('').write_text(render_public(root, source, sandbox))
                else:
                    shutil.copyfile(source, config / relative)
        # lazy.lua holds every plugin to the lock unless SHAFFY_LAZY_REFRESH is set.
        env = {
            **os.environ,
            'SHAFFY_LAZY_REFRESH': '1',
            **{f'XDG_{kind}_HOME': str(sandbox / kind.lower()) for kind in ('CONFIG', 'DATA', 'STATE', 'CACHE')},
        }
        # LazyVim discovers some plugins only once it is installed, so a second round completes the lock.
        for _ in range(2):
            result = run(
                ['nvim', '--headless', '+Lazy! sync', '+qa'],
                env=env,
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(f'nvim exited with {result.returncode}: {result.stderr.strip()[-400:]}')
        written = (config / 'lazy-lock.json').read_text()
        new, old = json.loads(written), json.loads(lock.read_text())
        if written == lock.read_text():
            return []
        lock.write_text(written)
    changes = [f'{len([n for n in new if n in old and new[n] != old[n]])} plugin(s) moved']
    changes += [
        f'{label}: {", ".join(names)}'
        for label, names in (('added', sorted(set(new) - set(old))), ('removed', sorted(set(old) - set(new))))
        if names
    ]
    return ['home/dot_config/nvim/lazy-lock.json', *changes]


def ruff_target(root, upstream):
    current = re.search(r'^target-version = "(py[0-9]+)"', (root / 'ruff.toml').read_text(), re.M).group(1)
    pin = Pin('ruff target, the system Python', current, upstream.python())
    pin.raise_to = lambda: [
        _edit(root, 'ruff.toml', f'target-version = "{current}"', f'target-version = "{pin.latest}"', 1)
    ]
    return [pin]


FAMILIES = (collections, yaru, better_bibtex, yabridge, ci_container, actions_checkout, lazyvim, ruff_target)


def check(root, upstream=None):
    """Every pin with its newest usable upstream version; a failed read is reported, not raised."""
    upstream = upstream or Upstream()
    pins = []
    for family in FAMILIES:
        try:
            pins += family(Path(root), upstream)
        except Exception as error:  # one unreachable upstream must not hide the others
            pins.append(Pin(family.__name__.replace('_', ' '), '?', error=f'{type(error).__name__}: {error}'))
    return pins
