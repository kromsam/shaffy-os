"""Invariants of the repository itself: the Git boundary of the private supplement,
licences and attribution, and no personal literals in the public tree."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy import literals  # noqa: E402

ALLOW = 'tests/public-literals.allow'
CODE = ('.py', '.sh', '.yml', '.yaml', '.tmpl', '.kdl', '.toml', '.j2', '.json')


def git(*args):
    return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True, check=False)


@unittest.skipUnless(shutil.which('git') and git('rev-parse', '--git-dir').returncode == 0,
                     'needs a git checkout')
class SupplementBoundaryTests(unittest.TestCase):
    def test_gitignore_ignores_the_supplement_directory_and_symlink(self):
        # No trailing slash: a trailing slash would miss a symlink named supplement.
        lines = (ROOT / '.gitignore').read_text().splitlines()
        self.assertIn('/supplement', lines)
        self.assertNotIn('/supplement/', lines)

    def test_no_tracked_supplement_path(self):
        tracked = git('ls-files', '-z').stdout.split('\0')
        self.assertEqual([p for p in tracked if p == 'supplement' or p.startswith('supplement/')], [])

    def test_no_gitlink_anywhere_and_no_symlink_at_supplement(self):
        entries = [line.split(None, 3) for line in git('ls-files', '-s').stdout.splitlines()]
        self.assertEqual([e[3] for e in entries if e[0] == '160000'], [], 'gitlinks are not allowed')
        self.assertEqual([e[3] for e in entries if e[0] == '120000' and e[3] == 'supplement'], [])

    def test_only_the_supplement_is_ignored_not_the_example(self):
        self.assertEqual(git('check-ignore', '-q', 'supplement/config.yaml').returncode, 0)
        self.assertEqual(git('check-ignore', '-q', 'supplement').returncode, 0)
        self.assertEqual(git('check-ignore', '-q', 'supplement.example/config.yaml').returncode, 1)


def tracked():
    return subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True, text=True,
                          check=True).stdout.split('\0')[:-1]


def exported(paths):
    """The paths the public export keeps: those not marked export-ignore in .gitattributes."""
    result = subprocess.run(['git', '-C', str(ROOT), 'check-attr', '-z', 'export-ignore', '--', *paths],
                            capture_output=True, text=True, check=True).stdout.split('\0')[:-1]
    ignored = {result[i] for i in range(0, len(result), 3) if result[i + 2] == 'set'}
    return [path for path in paths if path not in ignored]


class LicenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reuse = tomllib.loads((ROOT / 'REUSE.toml').read_text())
        cls.files = [path for path in tracked() if (ROOT / path).exists()]  # deletions not yet committed

    def annotations(self):
        for annotation in self.reuse['annotations']:
            paths = annotation['path']
            yield [paths] if isinstance(paths, str) else paths, annotation

    def test_the_project_is_mit_licensed(self):
        self.assertTrue((ROOT / 'LICENSE').read_text().startswith('MIT License\n\nCopyright (c) 2026 '))
        default = self.reuse['annotations'][0]
        self.assertEqual((default['path'], default['SPDX-License-Identifier']), ('**', 'MIT'))

    def test_every_licence_used_has_its_text_and_every_text_is_used(self):
        used = set()
        for _, annotation in self.annotations():
            expression = annotation['SPDX-License-Identifier']
            used |= set(re.split(r'\s+(?:AND|OR|WITH)\s+|[()]', expression)) - {''}
        for path in self.files:
            match = re.search(r'SPDX-License-' r'Identifier:\s*(\S+)', (ROOT / path).read_text(errors='replace')[:500])
            if match:
                used.add(match.group(1))
        texts = {p.stem for p in (ROOT / 'LICENSES').glob('*.txt')}
        self.assertEqual(texts, used)

    def test_every_annotated_path_is_a_public_tracked_file(self):
        public = set(exported(self.files))
        for paths, _ in self.annotations():
            for path in paths:
                if path != '**':
                    self.assertIn(path, public, f'REUSE.toml names {path}, which the export would not contain')

    def test_files_derived_from_cachyos_stay_gpl_with_their_notice(self):
        gpl = {path for paths, a in self.annotations() if a['SPDX-License-Identifier'] == 'GPL-3.0-only'
               for path in paths}
        noticed = {path for path in self.files if path.startswith('home/')
                   and 'Derived from CachyOS cachyos-niri-noctalia' in (ROOT / path).read_text(errors='replace')}
        self.assertEqual(noticed, gpl)
        self.assertEqual(len(gpl), 8)

    def test_modified_apache_files_say_so(self):
        holder = (ROOT / 'LICENSE').read_text().splitlines()[2].removeprefix('Copyright (c) ')
        for paths, annotation in self.annotations():
            if 'Apache-2.0' in annotation['SPDX-License-Identifier'] and holder in \
                    annotation['SPDX-FileCopyrightText']:
                for path in paths:
                    self.assertIn('Modified for Shaffy OS', (ROOT / path).read_text(), path)

    def test_nothing_without_a_licence_to_redistribute_is_tracked(self):
        self.assertEqual([p for p in self.files if p.startswith('vendor/openai')], [])
        self.assertFalse(any(p.endswith('ship-at-sea.jpg') for p in self.files))


@unittest.skipUnless(shutil.which('git') and git('rev-parse', '--git-dir').returncode == 0,
                     'needs a git checkout')
class PublicLiteralTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = literals.public_files(ROOT)
        cls.allow = literals.read_allowlist(ROOT / ALLOW)
        cls.static = literals.scan(ROOT, [f for f in cls.files if f != ALLOW], literals.static_rules())

    def report(self, hits):
        return '\n'.join(f'{h.path}:{h.line}: {h.rule}: {h.text}' for h in hits[:100]) + \
            (f'\n… and {len(hits) - 100} more' if len(hits) > 100 else '')

    def test_public_files_hold_no_personal_literals(self):
        hits = [h for h in self.static if not literals.allowed(h, self.allow)]
        if hits:
            self.fail('personal-looking values in public files; make them synthetic, or list a required '
                      f'one in {ALLOW}:\n' + self.report(hits))

    def test_public_files_hold_none_of_this_machine_identifiers(self):
        hits = literals.scan(ROOT, self.files, literals.environment_rules(ROOT))
        if hits:
            self.fail("this machine's account, host name, machine-id or Git identity appear in public "
                      'files:\n' + self.report(hits))

    def test_every_allowlist_entry_is_needed_and_explained(self):
        for entry in self.allow:
            rule, glob, text, reason = entry
            self.assertNotIn(rule, ('account', 'host', 'machine-id', 'git-identity'), entry)
            self.assertTrue(reason.strip(), entry)
            self.assertTrue(any(literals.allowed(h, [entry]) for h in self.static), f'unused entry: {entry}')

    def test_the_export_carries_no_transitional_block(self):
        self.assertEqual(literals.transitional_blocks(ROOT, self.files), [])


class LiteralScannerTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)

    def track(self):
        subprocess.run(['git', '-C', str(self.repo), 'add', '-A'], check=True)
        return literals.public_files(self.repo)

    def test_only_a_comment_starts_a_transitional_block(self):
        marker = 'Transitional' + ', until the migration converges: the old name.'
        (self.repo / 'a.py').write_text(f'# {marker}\nOLD = 1\n\nNEW = 2\n')
        (self.repo / 'b.md').write_text(f'<!-- {marker} -->\nOld text.\n')
        (self.repo / 'c.py').write_text(f'NAME = "{marker}"\n')
        self.assertEqual(literals.transitional_blocks(self.repo, self.track()), [('a.py', 1), ('b.md', 1)])
        _, lines = literals.exempt_lines('a.py', literals.lines(self.repo, 'a.py'))
        self.assertEqual(lines, {1, 2})

    def test_a_link_to_a_folder_is_scanned_as_its_target(self):
        (self.repo / 'folder').mkdir()
        (self.repo / 'folder/file').write_text('x\n')
        (self.repo / 'link').symlink_to('/home/tester/Projects')
        (self.repo / 'dirlink').symlink_to(self.repo / 'folder')
        files = self.track()
        self.assertIn('link', files)
        self.assertIn('dirlink', files)
        hits = literals.scan(self.repo, files, literals.static_rules())
        self.assertIn(('link', 'home-path'), {(h.path, h.rule) for h in hits})


@unittest.skipUnless(shutil.which('git') and git('rev-parse', '--git-dir').returncode == 0,
                     'needs a git checkout')
class ExportBoundaryTests(unittest.TestCase):
    def test_public_code_names_no_laptop_only_file(self):
        """The export's own selftest must not reach for a file the export leaves out."""
        public = literals.public_files(ROOT)
        everything = set(git('ls-files').stdout.splitlines())
        laptop_only = everything - set(public)
        found = []
        for path in public:
            if not path.endswith(CODE) or path == 'tests/test_repo_invariants.py':  # names them on purpose
                continue
            for number, line in enumerate(literals.lines(ROOT, path), 1):
                for name in re.findall(r'(?:scripts|files|roles|tests|packages)/[\w./-]*\w', line):
                    if name in laptop_only:
                        found.append(f'{path}:{number}: {name}')
        self.assertEqual(found, [])

    def test_the_laptop_tools_are_left_out(self):
        public = set(literals.public_files(ROOT))
        for path in ('scripts/configure-swap.py', 'scripts/configure-secure-boot.py', 'scripts/configure-plymouth.py',
                     'scripts/development-databases.py', 'nas-backup.yml', 'docs/IMPLEMENTATION-PLAN.md',
                     'tests/test_swap_guards.py', 'tests/test_container_restore.py'):
            self.assertNotIn(path, public)


if __name__ == '__main__':
    unittest.main()
