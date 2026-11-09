"""The versions this repository pins, where newer ones come from, and how to raise them.

`./bin/shaffy outdated` reports them; scripts/refresh-pins.py raises them on dev, in one
diff to review before a release. Like mise's `latest`, a release counts only once it is
three days old where the upstream records a date, as Galaxy does; git tags and the CI
image's tag carry none. yabridge is a reviewed source: it is reported, and its revision
changes by hand. ruff's target follows the system Python. Tools mise installs follow
`latest` and have no pin here.
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


class Upstream:
    """The reads from outside the checkout, in one place so the tests can replace them."""

    def __init__(self, now=None):
        self.now = now or datetime.datetime.now(datetime.UTC)

    def read(self, url):
        request = urllib.request.Request(url, headers={'User-Agent': 'shaffy-os-pins'})
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()

    def json(self, url):
        return json.loads(self.read(url))

    def refs(self, url, *patterns):
        """{ref: commit} from `git ls-remote`, with annotated tags peeled."""
        result = subprocess.run(
            ['git', 'ls-remote', url, *patterns], capture_output=True, text=True, timeout=60, check=True
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


# The workflows that run in the CI container: this repository's and the one supplements call.
WORKFLOWS = ('.github/workflows/selftest.yml', '.github/workflows/supplement.yml')


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
