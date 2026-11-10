"""./bin/shaffy profile check: an author's profile repository, checked as vendoring, a load and an
apply will read it (docs/PROFILES.md#write-a-profile).

A profile repository holds one profile per top-level folder with a profile.yaml in it, and a
licence. The check reads the repository and writes only private temporary supplements, which it
renders in the sandbox; it fetches nothing and changes nothing in the repository. It imports
vendor.py to build those supplements as vendoring does, never the other way.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from . import lint, literals, loader, profiles, sandbox
from .selection import SelectionError, resolve
from .vendor import LIMITS, ProfileError, _references, install

ALLOW = 'literals.allow'  # values the static rules may pass, in tests/public-literals.allow's format
LICENCES = ('LICENSE', 'LICENSE.md', 'LICENSE.txt')  # or a LICENSES/ folder, as REUSE keeps them
OWNER = 'author'  # the owner of every profile in the temporary supplements
SOURCE = 'https://example.invalid/this-repository'  # their source, where no https origin names it
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
    tracked = _tracked(path)
    if tracked is None:
        step = Step(
            'Repository',
            f'{path} is not a Git repository.',
            ('Vendoring reads a profile from a commit: git init the folder, then add and commit the profiles.',),
        )
        return Report(path, (), (step,))
    folders = sorted(
        manifest.parent for manifest in path.glob(f'*/{profiles.MANIFEST}') if not manifest.parent.name.startswith('.')
    )
    loaded, first = _profiles(checkout, folders)
    steps = [
        first,
        _files(tracked, loaded),
        _licence(path, tracked),
        _personal_values(path, supplement),
        _lint(path),
        _render(checkout, path, [folder for folder, data in loaded.items() if data is not None]),
    ]
    return Report(path, tuple(folder.name for folder in folders), tuple(steps))


def _tracked(path):
    """{path relative to PATH: (mode, size)} of every file Git tracks under PATH, or None outside
    a Git work tree. A link's size is its target's length; a submodule's is 0."""
    listed = subprocess.run(
        ['git', '-C', str(path), 'ls-files', '--stage', '-z'], capture_output=True, text=True, check=False
    )
    if listed.returncode:
        return None
    tracked = {}
    for record in listed.stdout.split('\0')[:-1]:
        info, _, name = record.partition('\t')
        mode = info.split()[0]
        target = path / name
        size = 0 if mode == '160000' or not target.is_file() else os.lstat(target).st_size
        tracked[name] = (mode, size)
    return tracked


def _profiles(checkout, folders):
    """({folder: its profile.yaml, or None when it does not load}, the Profiles step): each
    folder as a load checks a listed profile."""
    loaded, problems = {}, []
    for folder in folders:
        name = folder.name
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


def _files(tracked, loaded):
    """The Files step: what vendoring reads from a commit. Every file a profile names is tracked,
    no profile holds a link or a submodule, and each stays within vendor.LIMITS."""
    problems = []
    most_files, most_bytes = LIMITS
    for folder, data in loaded.items():
        name = folder.name
        inside = {relative: entry for relative, entry in tracked.items() if relative.startswith(f'{name}/')}
        for relative, (mode, _) in sorted(inside.items()):
            if mode in ('120000', '160000'):
                what = 'a link' if mode == '120000' else 'a submodule'
                problems.append(f'{relative}: {what}; a profile holds plain files, which vendoring copies')
        named = {profiles.MANIFEST, *(_references(data) if data else [])}
        problems += [
            f'{name}/{reference}: not tracked by Git; vendoring reads a commit, so add and commit it'
            for reference in sorted(named)
            if f'{name}/{reference}' not in tracked
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
        else 'every file a profile names is tracked by Git, and none is a link or a submodule.'
    )
    return Step('Files', summary, tuple(problems))


def _licence(path, tracked):
    """The Licence step: everyone who vendors a profile copies and redistributes its files."""
    found = [name for name in LICENCES if name in tracked]
    found += ['LICENSES/'] if any(relative.startswith('LICENSES/') for relative in tracked) else []
    if found:
        return Step('Licence', f'{", ".join(found)}.')
    untracked = [name for name in (*LICENCES, 'LICENSES') if (path / name).exists()]
    if untracked:
        problem = f'{untracked[0]} is not tracked by Git; add and commit it, since vendoring reads a commit.'
    else:
        problem = (
            'Everyone who vendors a profile copies its files into a repository of their own, so this one needs a '
            'licence that allows it: a LICENSE file or a LICENSES/ folder at its root. profile.example/LICENSE in '
            'Shaffy OS is the MIT licence with the holder left to fill in.'
        )
    return Step('Licence', 'missing.', (problem,))


def _personal_values(path, supplement):
    """The Personal values step: the static rules, which literals.allow may pass, this machine's
    identity and the supplement's private values, over every file Git tracks or would. A hit names
    its file, line and rule, never the value."""
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
    files = [name for name in literals.public_files(path) if name != ALLOW]
    hits = [hit for hit in literals.scan(path, files, literals.static_rules()) if not literals.allowed(hit, allow)]
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


def _lint(path):
    """The Lint step: STYLE.md's tools with this checkout's configuration, over every file Git
    tracks or would."""
    report = []
    status = lint.run(lint.files(path), root=path, out=report.append)
    if not status:
        return Step('Lint', "every file is clean by Shaffy OS's STYLE.md.")
    problems = [line for line in report if not line.endswith((' clean', 'Nothing to lint.'))]
    return Step('Lint', 'problems remain; ./bin/shaffy lint --fix PATH formats what it can.', tuple(problems))


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
    source = origin if origin.startswith('https://') else SOURCE
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
        listed, required = [], {}
        for folder in folders:
            data = loader.parse(folder / profiles.MANIFEST)
            named = {profiles.MANIFEST, *_references(data)}
            named |= {profiles.README} if (folder / profiles.README).is_file() else set()
            content = {name: (folder / name).read_bytes() for name in sorted(named)}
            profile_id = f'{OWNER}/{folder.name}'
            digests = {name: hashlib.sha256(blob).hexdigest() for name, blob in content.items()}
            locked = profiles.Locked(profile_id, source, folder.name, 'HEAD', '0' * 40, digests)
            try:
                install(root, profile_id, content, locked)
            except ProfileError as error:
                return 0, [str(error)]
            listed.append(profile_id)
            required |= dict.fromkeys(data.get('requires', []), 'managed')
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
