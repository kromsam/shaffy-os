"""Invariants of the repository itself: licences and attribution, no personal
literals in the public tree, no unreferenced script or document, Markdown links that
resolve and role files that name their role. `./bin/shaffy lint` holds scripts to the
system interpreter."""

from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy import literals

ALLOW = 'tests/public-literals.allow'


def git(*args):
    return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True, check=False)


def tracked():
    return subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True, text=True, check=True
    ).stdout.split('\0')[:-1]


class LicenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reuse = tomllib.loads((ROOT / 'REUSE.toml').read_text())
        cls.files = [path for path in tracked() if (ROOT / path).is_file()]  # deletions not yet committed

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
        public = set(self.files)
        for paths, _ in self.annotations():
            for path in paths:
                if path != '**':
                    self.assertIn(path, public, f'REUSE.toml names {path}, which is not a tracked file')

    def test_files_derived_from_cachyos_stay_gpl_with_their_notice(self):
        gpl = {
            path for paths, a in self.annotations() if a['SPDX-License-Identifier'] == 'GPL-3.0-only' for path in paths
        }
        noticed = {
            path
            for path in self.files
            if path.startswith('home/')
            and 'Derived from CachyOS cachyos-niri-noctalia' in (ROOT / path).read_text(errors='replace')
        }
        self.assertEqual(noticed, gpl)
        self.assertEqual(len(gpl), 7)

    def test_modified_apache_files_say_so(self):
        holder = (ROOT / 'LICENSE').read_text().splitlines()[2].removeprefix('Copyright (c) ')
        for paths, annotation in self.annotations():
            if 'Apache-2.0' in annotation['SPDX-License-Identifier'] and holder in annotation['SPDX-FileCopyrightText']:
                for path in paths:
                    self.assertIn('Modified for Shaffy OS', (ROOT / path).read_text(), path)

    def test_nothing_without_a_licence_to_redistribute_is_tracked(self):
        self.assertEqual([p for p in self.files if p.startswith('vendor/openai')], [])
        self.assertFalse(any(p.endswith('ship-at-sea.jpg') for p in self.files))


@unittest.skipUnless(shutil.which('git') and git('rev-parse', '--git-dir').returncode == 0, 'needs a git checkout')
class PublicLiteralTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = literals.public_files(ROOT)
        cls.allow = literals.read_allowlist(ROOT / ALLOW)
        cls.static = literals.scan(ROOT, [f for f in cls.files if f != ALLOW], literals.static_rules())

    def report(self, hits):
        return '\n'.join(f'{h.path}:{h.line}: {h.rule}: {h.text}' for h in hits[:100]) + (
            f'\n… and {len(hits) - 100} more' if len(hits) > 100 else ''
        )

    def test_public_files_hold_no_personal_literals(self):
        hits = [h for h in self.static if not literals.allowed(h, self.allow)]
        if hits:
            self.fail(
                'personal-looking values in public files; make them synthetic, or list a required '
                f'one in {ALLOW}:\n' + self.report(hits)
            )

    def test_public_files_hold_none_of_this_machine_identifiers(self):
        hits = literals.scan(ROOT, self.files, literals.environment_rules(ROOT))
        if hits:
            self.fail(
                "this machine's account, host name, machine-id or Git identity appear in public "
                'files:\n' + self.report(hits)
            )

    def test_every_allowlist_entry_is_needed_and_explained(self):
        for entry in self.allow:
            rule, glob, text, reason = entry
            self.assertNotIn(rule, ('account', 'host', 'machine-id', 'git-identity'), entry)
            self.assertTrue(reason.strip(), entry)
            self.assertTrue(any(literals.allowed(h, [entry]) for h in self.static), f'unused entry: {entry}')


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


def tracked_text():
    """{path: text} of every tracked text file that exists."""
    files = {}
    for path in git('ls-files', '-z').stdout.split('\0'):
        target = ROOT / path
        if path and target.is_file() and not target.is_symlink():
            try:
                files[path] = target.read_text()
            except UnicodeDecodeError:
                pass
    return files


def heading_slugs(text):
    """GitHub's anchors for a Markdown file's headings."""
    slugs, seen = set(), {}
    for line in re.sub(r'^```.*?^```', '', text, flags=re.M | re.S).splitlines():
        heading = re.match(r'#{1,6} (.+)', line)
        if heading:
            words = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', heading.group(1)).strip().lower()
            slug = re.sub(r'[^\w\- ]', '', words).replace(' ', '-')
            slugs.add(f'{slug}-{seen[slug]}' if slug in seen else slug)
            seen[slug] = seen.get(slug, 0) + 1
    return slugs


@unittest.skipUnless(shutil.which('git') and git('rev-parse', '--git-dir').returncode == 0, 'needs a git checkout')
class TidinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = tracked_text()

    def test_every_script_and_document_is_referenced(self):
        # A test alone does not count: something a person or the code uses names the file.
        for path in sorted(p for p in self.files if p.startswith(('scripts/', 'docs/'))):
            name = Path(path).name
            users = [
                other
                for other, text in self.files.items()
                if other != path and not other.startswith('tests/') and name in text
            ]
            self.assertTrue(users, f'nothing outside the tests names {path}; document it or delete it')

    def test_relative_markdown_links_and_anchors_resolve(self):
        for path, text in sorted(self.files.items()):
            if not path.endswith('.md'):
                continue
            prose = re.sub(r'`[^`\n]*`', '', re.sub(r'^```.*?^```', '', text, flags=re.M | re.S))
            for target in re.findall(r'\]\(([^)\s]+)\)', prose):
                if re.match(r'[a-z]+:', target):
                    continue
                file, _, anchor = target.partition('#')
                resolved = (ROOT / path).parent / file if file else ROOT / path
                self.assertTrue(resolved.exists(), f'{path}: {target} does not exist')
                if anchor and resolved.suffix == '.md':
                    self.assertIn(anchor, heading_slugs(resolved.read_text()), f'{path}: {target}')

    def test_every_file_a_role_writes_names_its_role(self):
        for path in sorted(self.files):
            parts = Path(path).parts
            if (
                parts[0] == 'roles'
                and len(parts) > 3
                and parts[2] in ('files', 'templates')
                and parts[-1] != 'daemon.json'
            ):
                head = self.files[path].splitlines()[:3]
                self.assertTrue(any(f'Managed by Shaffy OS (the {parts[1]} role)' in line for line in head), path)


if __name__ == '__main__':
    unittest.main()
