"""The versions this repository pins, where newer ones come from, and how to raise them.

`./bin/shaffy outdated` reports them; scripts/refresh-pins.py raises them on dev, in one
diff to review before a release. Like mise's `latest`, a release counts only once it is
three days old, where the upstream records a date (Galaxy, PyPI and GitHub releases; git
tags and the CI image's tag carry none). yabridge is a reviewed source: it is reported,
and its revision changes by hand.
"""
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
from typing import Callable, Optional
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
    latest: Optional[str] = None
    # Rewrites the files for `latest` and returns their paths; None: changed by hand.
    raise_to: Optional[Callable[[], list]] = None
    note: str = ''
    error: str = ''

    @property
    def behind(self):
        return not self.error and self.latest is not None and self.latest != self.current


class Upstream:
    """The network reads, in one place so the tests can replace them."""

    def __init__(self, now=None):
        self.now = now or datetime.datetime.now(datetime.timezone.utc)

    def read(self, url):
        headers = {'User-Agent': 'shaffy-os-pins'}
        if url.startswith('https://api.github.com/') and os.environ.get('GITHUB_TOKEN'):
            headers['Authorization'] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()

    def json(self, url):
        return json.loads(self.read(url))

    def refs(self, url, *patterns):
        """{ref: commit} from `git ls-remote`, with annotated tags peeled."""
        result = subprocess.run(['git', 'ls-remote', url, *patterns], capture_output=True, text=True,
                                timeout=60, check=True)
        refs = {}
        for line in result.stdout.splitlines():
            commit, ref = line.split('\t')
            peeled = ref.endswith('^{}')
            ref = ref.removesuffix('^{}')
            if peeled or ref not in refs:
                refs[ref] = commit
        return refs

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
        versions = [v['version'] for v in data['data']
                    if STABLE.fullmatch(v['version']) and upstream.old_enough(v['created_at'])]
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


def zotero_mcp(root, upstream):
    script = 'scripts/install-workspace-tools.sh'
    current = re.search(r"zotero-mcp-server\[[^\]]*\]==([0-9.]+)'", (root / script).read_text()).group(1)
    pin = Pin('Zotero MCP (zotero-mcp-server)', current)
    releases = upstream.json('https://pypi.org/pypi/zotero-mcp-server/json')['releases']
    usable = [version for version, files in releases.items()
              if STABLE.fullmatch(version) and files and not all(f.get('yanked') for f in files)
              and upstream.old_enough(min(f['upload_time_iso_8601'] for f in files))]
    pin.latest = max(usable, key=numeric, default=current)
    pin.raise_to = lambda: [
        _edit(root, script, f"]=={current}'", f"]=={pin.latest}'", 1),
        _edit(root, 'docs/WORKSPACE-TOOLS.md', f'`zotero-mcp-server[semantic,pdf]` {current} ',
              f'`zotero-mcp-server[semantic,pdf]` {pin.latest} ', 1)]
    return [pin]


def qpdf(root, upstream):
    script, launcher = 'scripts/install-publishing-tools.sh', 'home/private_dot_local/bin/executable_qpdf'
    text = (root / script).read_text()
    current = re.search(r'qpdf-([0-9.]+)-bin-linux-x86_64\.zip', text).group(1)
    digest = re.search(r"hexdigest\(\) == '([0-9a-f]{64})'", text).group(1)
    pin = Pin('qpdf', current)
    releases = [r for r in upstream.json('https://api.github.com/repos/qpdf/qpdf/releases?per_page=20')
                if not r['draft'] and not r['prerelease'] and STABLE.fullmatch(r['tag_name'].removeprefix('v'))
                and upstream.old_enough(r['published_at'])]
    newest = max(releases, key=lambda r: numeric(r['tag_name']), default=None)
    pin.latest = newest['tag_name'].removeprefix('v') if newest else current

    def raise_to():
        name = f'qpdf-{pin.latest}-bin-linux-x86_64.zip'
        asset = next(a for a in newest['assets'] if a['name'] == name)
        sha256 = (asset.get('digest') or '').removeprefix('sha256:') or \
            hashlib.sha256(upstream.read(asset['browser_download_url'])).hexdigest()
        _edit(root, script, digest, sha256, 1)
        return [_edit(root, script, current, pin.latest), _edit(root, launcher, current, pin.latest)]
    pin.raise_to = raise_to
    return [pin]


def yaru(root, upstream):
    script = 'scripts/install-yaru-icons.py'
    text = (root / script).read_text()
    current = re.search(r"VERSION = '([^']+)'", text).group(1)
    digest = re.search(r"SHA256 = '([0-9a-f]{64})'", text).group(1)
    pin = Pin('Yaru icons', current)
    # Keep the release line in use: Ubuntu's `…ubuntu` tags or upstream's plain ones.
    flavour = re.compile(r'[0-9]+(?:\.[0-9]+)+' + re.escape(re.sub(r'^[0-9.]+', '', current)))
    tags = [ref.removeprefix('refs/tags/') for ref in upstream.refs('https://github.com/ubuntu/yaru.git', 'refs/tags/*')]
    pin.latest = max((t for t in tags if flavour.fullmatch(t)), key=numeric, default=current)

    def raise_to():
        archive = upstream.read(f'https://github.com/ubuntu/yaru/archive/refs/tags/{pin.latest}.tar.gz')
        sha256 = hashlib.sha256(archive).hexdigest()
        changed = [_edit(root, path, current, pin.latest)
                   for path in (script, 'README.md', 'docs/OMARCHY-IMPLEMENTATION.md')]
        _edit(root, script, digest, sha256, 1)
        _edit(root, 'docs/OMARCHY-IMPLEMENTATION.md', digest, sha256, 1)
        return changed
    pin.raise_to = raise_to
    return [pin]


def yabridge(root, upstream):
    text = (root / 'scripts/build-audio-bridge.sh').read_text()
    current = re.search(r'revision=([0-9a-f]{40})', text).group(1)
    branch = re.search(r'--branch (\S+)', text).group(1)
    head = upstream.refs('https://github.com/robbert-vdh/yabridge.git', f'refs/heads/{branch}')
    latest = head.get(f'refs/heads/{branch}', current)
    return [Pin(f'yabridge ({branch})', current[:12], latest[:12],
                note='a reviewed source: read the upstream changes, then set the revision in '
                     'scripts/build-audio-bridge.sh and docs/MAINTENANCE.md by hand')]


def ci_container(root, upstream):
    workflow = '.github/workflows/selftest.yml'
    current = re.search(r'cachyos/cachyos@(sha256:[0-9a-f]{64})', (root / workflow).read_text()).group(1)
    tag = upstream.json('https://hub.docker.com/v2/repositories/cachyos/cachyos/tags/latest')
    pin = Pin('CI container cachyos/cachyos:latest', current[7:19], tag['digest'][7:19])
    pin.raise_to = lambda: [_edit(root, workflow, current, tag['digest'], 1),
                            _edit(root, 'tests/tool-versions.json', current, tag['digest'], 1)]
    return [pin]


def actions_checkout(root, upstream):
    workflow = '.github/workflows/selftest.yml'
    commit, current = re.search(r'actions/checkout@([0-9a-f]{40}) # (v[0-9.]+)', (root / workflow).read_text()).groups()
    refs = upstream.refs('https://github.com/actions/checkout.git', 'refs/tags/v*')
    releases = {ref.removeprefix('refs/tags/'): sha for ref, sha in refs.items()
                if re.fullmatch(r'refs/tags/v[0-9]+\.[0-9]+\.[0-9]+', ref)}
    pin = Pin('GitHub action actions/checkout', current, max(releases, key=numeric, default=current))
    pin.raise_to = lambda: [_edit(root, workflow, f'actions/checkout@{commit} # {current}',
                                  f'actions/checkout@{releases[pin.latest]} # {pin.latest}')]
    return [pin]


def lazyvim(root, upstream, run=subprocess.run):
    lock = 'home/dot_config/nvim/lazy-lock.json'
    stamp = subprocess.run(['git', '-C', str(root), 'log', '-1', '--format=%cI', '--', lock],
                           capture_output=True, text=True, check=False).stdout.strip()
    refreshed = datetime.datetime.fromisoformat(stamp) if stamp else upstream.now
    pin = Pin('LazyVim plugin lock', f'refreshed {refreshed.date()}',
              note='plugins move continuously; refresh-pins updates them in a sandbox')
    pin.latest = 'due for a refresh' if upstream.now - refreshed > LOCK_AGE else pin.current
    pin.raise_to = lambda: refresh_lazyvim(root, run)
    return [pin]


def refresh_lazyvim(root, run=subprocess.run):
    """Update every plugin in a throwaway Neovim home and keep the lock it writes."""
    lock = root / 'home/dot_config/nvim/lazy-lock.json'
    with tempfile.TemporaryDirectory(prefix='shaffy-lazyvim-') as directory:
        sandbox = Path(directory)
        config = sandbox / 'config/nvim'
        for source in (root / 'home/dot_config/nvim').rglob('*'):
            if source.is_file():
                relative = Path(*(part.replace('dot_', '.', 1) if part.startswith('dot_') else part
                                  for part in source.relative_to(root / 'home/dot_config/nvim').parts))
                (config / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, config / relative)
        # lazy.lua holds every plugin to the lock unless SHAFFY_LAZY_REFRESH is set.
        env = {**os.environ, 'SHAFFY_LAZY_REFRESH': '1',
               **{f'XDG_{kind}_HOME': str(sandbox / kind.lower()) for kind in ('CONFIG', 'DATA', 'STATE', 'CACHE')}}
        # LazyVim discovers some plugins only once it is installed, so a second round completes the lock.
        for _ in range(2):
            result = run(['nvim', '--headless', '+Lazy! sync', '+qa'], env=env, capture_output=True, text=True,
                         timeout=900, check=False)
            if result.returncode:
                raise RuntimeError(f'nvim exited with {result.returncode}: {result.stderr.strip()[-400:]}')
        written = (config / 'lazy-lock.json').read_text()
        new, old = json.loads(written), json.loads(lock.read_text())
        if written == lock.read_text():
            return []
        lock.write_text(written)
    changes = [f'{len([n for n in new if n in old and new[n] != old[n]])} plugin(s) moved']
    changes += [f'{label}: {", ".join(names)}' for label, names in
                (('added', sorted(set(new) - set(old))), ('removed', sorted(set(old) - set(new)))) if names]
    return ['home/dot_config/nvim/lazy-lock.json', *changes]


FAMILIES = (collections, zotero_mcp, qpdf, yaru, yabridge, ci_container, actions_checkout, lazyvim)


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
