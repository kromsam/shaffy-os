"""./bin/shaffy profile check: an author's profile repository, checked as vendoring, a load and an
apply will read it (docs/PROFILES.md#write-a-profile).

A profile repository holds one profile per top-level folder with a profile.yaml in it, and a
licence. The check reads the repository and runs nothing it brings: it lints a copy of what
vendoring reads, and writes only private temporary supplements, which it renders in the sandbox.
It fetches nothing and changes nothing in the repository. It imports vendor.py to build those
supplements as vendoring does, never the other way.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from . import lint, literals, loader, pins, profiles, sandbox
from .selection import SelectionError, resolve
from .vendor import LIMITS, ProfileError, _references, _write_lock, source_url

ALLOW = 'literals.allow'  # values the static rules may pass, in tests/public-literals.allow's format
LICENCES = ('LICENSE', 'LICENSE.md', 'LICENSE.txt')  # or a LICENSES/ folder, as REUSE keeps them
OWNER = 'author'  # the owner of every profile in the temporary supplements
SOURCE = 'https://example.invalid/this-repository'  # their source, where origin is no plain https URL
TOPIC = 'shaffy-os-profiles'
LIST = 'docs/PROFILES.md#profile-repositories'


@dataclass(frozen=True)
class Step:
    """One step of the check: its line, and what it found."""

    name: str
    summary: str
    problems: tuple = ()  # each fails the check
    notes: tuple = ()  # each is worth reading and fails nothing


@dataclass(frozen=True)
class Report:
    path: Path
    names: tuple  # the profiles, in folder order
    steps: tuple

    @property
    def problems(self):
        return sum(len(step.problems) for step in self.steps)

    def lines(self):
        """The report as the author reads it; the publish steps once nothing fails."""
        found = ', '.join(self.names) or 'none'
        lines = [f'Profile repository {self.path}: {len(self.names)} profile{"s" * (len(self.names) != 1)} ({found}).']
        for step in self.steps:
            lines.append(f'{step.name}: {step.summary}')
            lines += [f'  {problem}' for problem in step.problems]
            lines += [f'  Note: {note}' for note in step.notes]
        if not self.problems:
            lines += [
                'The check passed. To publish the repository:',
                '  1. Push it to a public repository.',
                f'  2. Add the topic {TOPIC} to it, so people find it.',
                f'  3. Propose it for the list in {LIST}: a pull request against dev of kromsam/shaffy-os.',
            ]
        lines.append(
            'A rule here that refuses something it should not? ./bin/shaffy profile propose --to shaffy '
            '[--key KEY] drafts the issue for Shaffy OS.'
        )
        return lines


def check_repository(checkout, path, supplement=None):
    """Check the profile repository at PATH; returns a Report. SUPPLEMENT names the supplement
    whose denylist and identifying settings the scan adds, instead of the bound one."""
    checkout, path = Path(checkout), Path(path).resolve()
    committed = _committed(path)
    if committed is None:
        step = Step(
            'Repository',
            f'{path} is not in a Git repository with a commit.',
            ('Vendoring reads a profile from a commit: git init the folder, then add and commit the profiles.',),
        )
        return Report(path, (), (step,))
    folders = sorted(
        manifest.parent for manifest in path.glob(f'*/{profiles.MANIFEST}') if not manifest.parent.name.startswith('.')
    )
    loaded, first = _profiles(checkout, folders)
    steps = [
        first,
        _files(committed, loaded),
        _licence(path, committed),
        _personal_values(path, supplement),
        _lint(loaded),
        _render(checkout, path, [folder for folder, data in loaded.items() if data is not None]),
    ]
    return Report(path, tuple(folder.name for folder in folders), tuple(steps))


def _committed(path):
    """{path relative to PATH: (mode, size)} of every file under PATH in the last commit, which
    vendoring reads, or None when PATH is in no Git work tree with a commit. A name that is not
    UTF-8 keeps its bytes (os.fsdecode). A link's size is its target's length; a submodule's is 0."""
    listed = subprocess.run(
        ['git', '-C', str(path), 'ls-tree', '-r', '-l', '-z', 'HEAD'], capture_output=True, check=False
    )
    if listed.returncode:
        return None
    committed = {}
    for record in listed.stdout.split(b'\0')[:-1]:
        info, _, name = record.partition(b'\t')
        mode, _, _, size = info.decode().split()
        committed[os.fsdecode(name)] = (mode, 0 if size == '-' else int(size))
    return committed


def _vendored(folder, data):
    """What vendoring reads of FOLDER that is there, relative to it: profile.yaml, the files DATA
    names (its profile.yaml as read, None when it does not load) and README.md."""
    named = {profiles.MANIFEST, profiles.README, *(_references(data) if data else [])}
    return sorted(name for name in named if (folder / name).is_file())


def _profiles(checkout, folders):
    """({folder: its profile.yaml, or None when it does not load}, the Profiles step): each
    folder as a load checks a listed profile. A folder that is a link loads nothing: the Files
    step names it."""
    loaded, problems = {}, []
    for folder in folders:
        name = folder.name
        if folder.is_symlink():
            loaded[folder] = None
            continue
        if not profiles.ID.match(f'{OWNER}/{name}'):
            problems.append(
                f"{name}/: not a profile's name, which is its folder's: lower-case letters, digits and hyphens, "
                'starting with a letter or digit, at most 63'
            )
            loaded[folder] = None
            continue
        found = loader.check_profile(checkout, folder, f'{name}/{profiles.MANIFEST}')
        problems += [problem.replace(f'{folder}/', f'{name}/') for problem in found]
        loaded[folder] = None if found else loader.parse(folder / profiles.MANIFEST)
    if not folders:
        problems.append(
            f'a profile is a folder at the top of the repository with a {profiles.MANIFEST} in it; '
            'profile.example/example/ in Shaffy OS is one'
        )
    summary = f'{len(problems)} problem{"s" * (len(problems) != 1)}.' if problems else 'each loads as a vendored copy.'
    return loaded, Step('Profiles', summary, tuple(problems))


def _files(committed, loaded):
    """The Files step: what vendoring reads from the last commit. Every file a profile names is in
    it, no profile is or holds a link or a submodule, no name is one vendoring refuses
    (pins.name_problem), and each profile stays within vendor.LIMITS."""
    problems = []
    most_files, most_bytes = LIMITS
    for folder, data in loaded.items():
        name = folder.name
        if folder.is_symlink():
            problems.append(f'{name}/: a link; a profile is a folder of plain files, which vendoring copies')
            continue
        inside = {relative: entry for relative, entry in committed.items() if relative.startswith(f'{name}/')}
        for relative, (mode, _) in sorted(inside.items()):
            refused = pins.name_problem(os.fsencode(relative.removeprefix(f'{name}/')))
            if refused:
                problems.append(f'{name}/: {refused}, so vendoring refuses the profile')
            elif mode in ('120000', '160000'):
                what = 'a link' if mode == '120000' else 'a submodule'
                problems.append(f'{relative}: {what}; a profile holds plain files, which vendoring copies')
        named = {profiles.MANIFEST, *(_references(data) if data else [])}
        problems += [
            f'{name}/{reference}: not in the last commit; vendoring reads a commit, so add and commit it'
            for reference in sorted(named)
            if f'{name}/{reference}' not in committed
        ]
        size = sum(entry[1] for entry in inside.values())
        if len(inside) > most_files or size > most_bytes:
            problems.append(
                f'{name}/: {len(inside)} files of {size} bytes; a profile holds at most {most_files} files and '
                f'{most_bytes} bytes, since every apply copies it'
            )
    summary = (
        f'{len(problems)} problem{"s" * (len(problems) != 1)}.'
        if problems
        else 'every file a profile names is in the last commit, and none is a link or a submodule.'
    )
    return Step('Files', summary, tuple(problems))


def _licence(path, committed):
    """The Licence step: everyone who vendors a profile copies and redistributes its files."""
    found = [name for name in LICENCES if name in committed]
    found += ['LICENSES/'] if any(relative.startswith('LICENSES/') for relative in committed) else []
    if found:
        return Step('Licence', f'{", ".join(found)}.')
    uncommitted = [name for name in (*LICENCES, 'LICENSES') if (path / name).exists()]
    if uncommitted:
        problem = f'{uncommitted[0]} is not in the last commit; add and commit it, since vendoring reads a commit.'
    else:
        problem = (
            'Everyone who vendors a profile copies its files into a repository of their own, so this one needs a '
            'licence that allows it: a LICENSE file or a LICENSES/ folder at its root. profile.example/LICENSE in '
            'Shaffy OS is the MIT licence with the holder left to fill in.'
        )
    return Step('Licence', 'missing.', (problem,))


def _personal_values(path, supplement):
    """The Personal values step: the static rules, which literals.allow may pass, this machine's
    identity and the supplement's private values, over every file Git tracks or would, literals.allow
    by the private rules alone. A hit names its file, line and rule, never the value."""
    problems, notes = [], []
    try:
        allow = literals.read_allowlist(path / ALLOW) if (path / ALLOW).is_file() else []
    except ValueError as error:
        problems.append(str(error).replace(f'{path}/', ''))
        allow = []
    private, used = literals.environment_rules(path), "the static rules and this machine's identity"
    try:
        selection = resolve(supplement)
        if selection['mode'] == 'supplement':
            root = Path(selection['path'])
            private += literals.supplement_rules(root, loader.parse(root / 'config.yaml'))
            used = "the static rules, this machine's identity and the supplement's denylist and settings"
        else:
            notes.append(
                'no supplement is bound, so its denylist and settings were not used; --supplement PATH names one'
            )
    except (SelectionError, loader.SettingsError, ValueError) as error:
        problems.append(f"the supplement's private values cannot be read, so the scan would miss them: {error}")
    files = literals.public_files(path)
    hits = [
        hit
        for hit in literals.scan(path, [name for name in files if name != ALLOW], literals.static_rules())
        if not literals.allowed(hit, allow)
    ]
    hits += literals.scan(path, files, private)
    problems += sorted({f'{hit.path}:{hit.line}: {hit.rule}' for hit in hits})
    if hits:
        summary = (
            f'{len({(hit.path, hit.line) for hit in hits})} line(s) look personal (line 0 is the file name; '
            f'the value is not shown). Replace each, or list a synthetic one in {ALLOW}.'
        )
    elif problems:
        summary = f'{len(problems)} problem{"s" * (len(problems) != 1)}.'
    else:
        summary = f'none found in {len(files)} files, by {used}.'
    return Step('Personal values', summary, tuple(problems), tuple(notes))


def _lint(loaded):
    """The Lint step: STYLE.md's tools, each given this checkout's configuration, over a copy of
    what vendoring reads of each profile in a temporary folder, so nothing the repository brings
    runs. The copy leaves out links and every name that starts with a dot, which a tool would read
    as its configuration and markdownlint-cli2 would run as code; lint.run's FOREIGN keeps
    ansible-lint from linting the copy as a project, which imports plugins."""
    with tempfile.TemporaryDirectory(prefix='shaffy-profile-lint-') as directory:
        copy, paths = Path(directory), []
        for folder, data in loaded.items():
            for name in _vendored(folder, data) if not folder.is_symlink() else []:
                if (folder / name).is_symlink() or any(part.startswith('.') for part in name.split('/')):
                    continue
                (copy / folder.name / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(folder / name, copy / folder.name / name)
                paths.append(f'{folder.name}/{name}')
        report = []
        status = lint.run(paths, root=copy, out=report.append, foreign=True)
    if not status:
        return Step('Lint', f"the {len(paths)} files vendoring reads are clean by Shaffy OS's STYLE.md.")
    problems = [
        line.replace(f'{directory}/', '') for line in report if not line.endswith((' clean', 'Nothing to lint.'))
    ]
    return Step('Lint', "problems remain in the files vendoring reads, by STYLE.md's tools.", tuple(problems))


def _render(checkout, path, folders):
    """The Render step: each profile alone, then all of them together in folder order, in a private
    temporary supplement as vendoring leaves one, rendered for a synthetic user in the sandbox.
    Profiles that conflict together are a note: a person who lists them both settles it in config.yaml."""
    if not folders:
        return Step('Render', 'nothing renders until a profile loads.')
    problems, notes, rendered = [], [], []
    groups = [[folder] for folder in folders] + ([folders] if len(folders) > 1 else [])
    origin = subprocess.run(
        ['git', '-C', str(path), 'remote', 'get-url', 'origin'], capture_output=True, text=True, check=False
    ).stdout.strip()
    try:
        source = source_url(origin)[0] if origin.startswith('https://') else SOURCE
    except ProfileError:  # a user name or a token in it, which no message may show
        source = SOURCE
    for group in groups:
        alone = len(group) == 1
        label = f'{group[0].name} alone' if alone else 'all together'
        try:
            files, found = _render_one(checkout, group, source)
        except sandbox.RenderError as error:
            problems.append(f'{label}: the render could not run: {error}')
            continue
        if not found:
            rendered.append(f'{label}: {files} files')
        elif alone:
            problems += [f'{label}: {problem}' for problem in found]
        else:
            notes += [f'{label}: {problem}' for problem in found]
    if problems:
        summary = f'{len(problems)} problem{"s" * (len(problems) != 1)}.'
    else:
        summary = (
            f'rendered for a synthetic user ({"; ".join(rendered)}); Niri, Noctalia and Fish validate, and a second '
            'apply changes nothing.'
        )
    return Step('Render', summary, tuple(problems), tuple(notes))


def _render_one(checkout, folders, source):
    """(files, problems) of FOLDERS' profiles, vendored from SOURCE and listed in that order in a
    temporary supplement."""
    with tempfile.TemporaryDirectory(prefix='shaffy-profile-check-') as directory:
        root = Path(directory)
        lock, required = {}, {}
        for folder in folders:
            data = loader.parse(folder / profiles.MANIFEST)
            profile_id = f'{OWNER}/{folder.name}'
            digests = {}
            for name in _vendored(folder, data):
                blob = (folder / name).read_bytes()
                (root / profiles.FOLDER / profile_id / name).parent.mkdir(parents=True, exist_ok=True)
                (root / profiles.FOLDER / profile_id / name).write_bytes(blob)
                digests[name] = hashlib.sha256(blob).hexdigest()
            lock[profile_id] = profiles.Locked(profile_id, source, folder.name, 'HEAD', '0' * 40, digests)
            required |= dict.fromkeys(data.get('requires', []), 'managed')
        _write_lock(root, lock)
        listed = list(lock)
        config = {'schema_version': 1, 'supplement_id': 'profile-check', 'profiles': listed}
        if required:
            config['integrations'] = required
        (root / 'config.yaml').write_text(json.dumps(config, indent=2) + '\n')  # JSON is YAML
        try:
            loader.load(checkout, supplement=root)
        except (SelectionError, loader.SettingsError) as error:
            return 0, [_relative(problem, root) for problem in getattr(error, 'problems', [str(error)])]
        result = sandbox.render(checkout, supplement=root)
        return result['files'], [_relative(problem, root) for problem in result['problems']]


def _relative(problem, root):
    """PROBLEM with the temporary supplement's paths written as the repository's."""
    return problem.replace(f'{root}/{profiles.FOLDER}/{OWNER}/', '').replace(f'{root}/', '')
