"""The profile commands and their reads of a profile's repository, against local Git repositories.

Every repository is built in a temporary folder and served as a bare copy over file://, so no
test reaches the network, and every git reads no configuration of this machine's.
"""

import hashlib
import json
import os
from pathlib import Path
import pty
import random
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shaffy import loader, pins, profiles, sharing, vendor
from support import vendor_profile

LIMITS = (200, 8 * 2**20)
GIT_ENV = {
    'GIT_CONFIG_GLOBAL': os.devnull,
    'GIT_CONFIG_NOSYSTEM': '1',
    'GIT_AUTHOR_NAME': 'Example Author',
    'GIT_AUTHOR_EMAIL': 'author@example.com',
    'GIT_COMMITTER_NAME': 'Example Author',
    'GIT_COMMITTER_EMAIL': 'author@example.com',
}
# A host that speaks only the first protocol and serves no commit that no branch or tag names.
REFUSES_BY_HASH = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'protocol.version', 'GIT_CONFIG_VALUE_0': '0'}
# The source the commands are given; git reads it from the local repository instead.
SOURCE = 'https://example.test/alice/profiles'
ISSUES = 'https://example.test/alice/profiles'  # not GitHub: the repository itself
NOTES = {
    'notes/profile.yaml': (
        'schema_version: 1\n'
        'description: Synthetic notes for the tests.\n'
        'overrides:\n  fish: shell/notes.fish\n'
        'assets:\n  "~/.local/share/example-notes/style.csl": style.csl\n'
    ),
    'notes/shell/notes.fish': 'set -g example_notes 1\n',
    'notes/style.csl': '<style/>\n',
    'notes/README.md': 'Synthetic notes.\n',
    'notes/tests/check.sh': 'exit 0\n',
    'keys/profile.yaml': 'schema_version: 1\ndescription: Synthetic keys.\npackages:\n  extra: [example-keys]\n',
}


def git(*arguments, stdin=None):
    return subprocess.run(
        ['git', *map(str, arguments)], input=stdin, capture_output=True, text=True, check=True
    ).stdout.strip()


class Repository:
    """An author's profile repository: a working copy, and the bare copy a host would serve."""

    def __init__(self, directory):
        self.work = Path(directory) / 'work'
        self.served = Path(directory) / 'served.git'
        git('init', '-q', '-b', 'main', self.work)
        git('init', '-q', '--bare', '-b', 'main', self.served)
        self.url = self.served.as_uri()

    def commit(self, message, files=(), remove=()):
        """Write FILES ({path: text}), delete REMOVE, commit and publish; returns the commit."""
        for path, text in dict(files).items():
            (self.work / path).parent.mkdir(parents=True, exist_ok=True)
            (self.work / path).write_text(text)
        for path in remove:
            (self.work / path).unlink()
        git('-C', self.work, 'add', '-A')
        git('-C', self.work, 'commit', '-q', '--allow-empty', '-m', message)
        git('-C', self.work, 'push', '-q', '--force', self.served, 'main')
        return git('-C', self.work, 'rev-parse', 'HEAD')

    def rewind(self, commit):
        """Reset the author's branch to COMMIT, as a rewritten history does."""
        git('-C', self.work, 'reset', '-q', '--hard', commit)

    def craft(self, folder, entries):
        """Publish, on a branch of its own, a commit whose FOLDER holds ENTRIES ({name: mode}) that
        no working copy could commit; returns the commit."""
        blob = git('-C', self.served, 'hash-object', '-w', '--stdin', stdin='x\n')
        inner = git('-C', self.served, 'mktree', stdin=f'100644 blob {blob}\tconfig\n')
        lines = [f'100644 blob {blob}\tprofile.yaml']
        for name, mode in entries.items():
            kind, target = ('tree', inner) if mode == '040000' else ('blob', blob)
            lines.append(f'{mode} {kind} {target}\t{name}')
        tree = git('-C', self.served, 'mktree', stdin='\n'.join(lines) + '\n')
        top = git('-C', self.served, 'mktree', stdin=f'040000 tree {tree}\t{folder}\n')
        commit = git('-C', self.served, 'commit-tree', top, '-m', 'crafted')
        git('-C', self.served, 'update-ref', f'refs/heads/crafted-{commit[:7]}', commit)
        return commit


class GitCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.repository = Repository(self.directory / 'author')
        self.upstream = pins.Upstream()
        self.addCleanup(self.upstream.close)


class UpstreamTests(GitCase):
    def test_tree_holds_one_commits_files_of_one_folder(self):
        first = self.repository.commit(
            'Add notes', {'notes/profile.yaml': 'one\n', 'notes/styles/a.csl': 'a\n', 'keys/profile.yaml': 'k\n'}
        )
        self.repository.commit('Change notes', {'notes/profile.yaml': 'two\n'})
        tree = self.upstream.tree(self.repository.url, first, 'notes', LIMITS)
        self.assertEqual(tree, {'profile.yaml': b'one\n', 'styles/a.csl': b'a\n'})

    def test_a_host_that_refuses_a_commit_by_hash_gets_a_clone(self):
        older = self.repository.commit('Add notes', {'notes/profile.yaml': 'one\n'})
        self.repository.commit('Change notes', {'notes/profile.yaml': 'two\n'})
        probe = self.directory / 'probe.git'
        git('init', '-q', '--bare', probe)
        with mock.patch.dict(os.environ, REFUSES_BY_HASH):
            refused = subprocess.run(
                ['git', '--git-dir', probe, 'fetch', '--depth', '1', self.repository.url, older],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertIn('not allow request for unadvertised object', refused.stderr)  # the host refuses it
            tree = self.upstream.tree(self.repository.url, older, 'notes', LIMITS)
        self.assertEqual(tree, {'profile.yaml': b'one\n'})

    def test_a_commit_no_branch_or_tag_holds_is_named(self):
        self.repository.commit('Add notes', {'notes/profile.yaml': 'one\n'})
        with self.assertRaisesRegex(ValueError, f'{self.repository.url}: no branch or tag holds the commit 1{{40}}'):
            self.upstream.tree(self.repository.url, '1' * 40, 'notes', LIMITS)

    def test_a_missing_folder_is_named(self):
        commit = self.repository.commit('Add notes', {'notes/profile.yaml': 'one\n'})
        with self.assertRaisesRegex(ValueError, 'keys/ in .* at [0-9a-f]{7}: no such folder'):
            self.upstream.tree(self.repository.url, commit, 'keys', LIMITS)

    def test_links_submodules_and_unsafe_names_are_refused(self):
        self.repository.commit('Add notes', {'notes/profile.yaml': 'one\n'})
        os.symlink('profile.yaml', self.repository.work / 'notes/link.yaml')
        link = self.repository.commit('Add a link')
        work = self.repository.work
        git('-C', work, 'rm', '-q', 'notes/link.yaml')
        git('-C', work, 'update-index', '--add', '--cacheinfo', f'160000,{link},notes/vendor')
        git('-C', work, 'commit', '-q', '-m', 'Add a submodule')
        git('-C', work, 'push', '-q', self.repository.served, 'main')
        submodule = git('-C', work, 'rev-parse', 'HEAD')
        cases = {
            'a link': (link, 'link.yaml is a link'),
            'a submodule': (submodule, 'vendor is a submodule'),
            # git's own check of every object it receives refuses these trees before tree() reads them.
            '.git': (self.repository.craft('notes', {'.git': '040000'}), 'fsck error'),
            '.GIT': (self.repository.craft('notes', {'.GIT': '040000'}), 'fsck error'),
            '..': (self.repository.craft('notes', {'..': '040000'}), 'fsck error'),
        }
        for case, (commit, message) in cases.items():
            with self.subTest(case), self.assertRaisesRegex(ValueError, message):
                self.upstream.tree(self.repository.url, commit, 'notes', LIMITS)

    def test_a_name_with_a_control_or_format_character_is_refused_without_showing_it(self):
        self.repository.commit('Add notes', {'notes/profile.yaml': 'one\n'})
        # An OSC 52 sequence would replace the clipboard in the terminal that printed it.
        for name, mode in (('\x1b]52;c;ZXhhbXBsZQ==\x07', '120000'), ('a\u202eb.txt', '100644'), ('\x9bx', '100644')):
            commit = self.repository.craft('notes', {name: mode})
            with self.subTest(name=ascii(name)):
                with self.assertRaisesRegex(ValueError, 'a control or format character') as caught:
                    self.upstream.tree(self.repository.url, commit, 'notes', LIMITS)
                self.assertEqual(pins.printable(str(caught.exception)), str(caught.exception))

    def test_a_name_git_reads_is_refused(self):
        self.repository.commit('Add notes', {'notes/profile.yaml': 'one\n'})
        for name, mode in (
            ('.gitattributes', '100644'),
            ('.gitignore', '100644'),
            ('.GitModules', '100644'),
            ('.github', '040000'),
        ):
            commit = self.repository.craft('notes', {name: mode})
            with self.subTest(name), self.assertRaisesRegex(ValueError, 'is not a name a profile may hold'):
                self.upstream.tree(self.repository.url, commit, 'notes', LIMITS)

    def test_only_the_folders_files_are_fetched(self):
        git('-C', self.repository.served, 'config', 'uploadpack.allowFilter', 'true')  # as public hosts serve
        (self.repository.work / 'other').mkdir()
        (self.repository.work / 'other/large.bin').write_bytes(os.urandom(2 * 2**20))
        old = self.repository.commit('Add notes beside a large file', {'notes/profile.yaml': 'one\n'})
        new = self.repository.commit('Change notes', {'notes/profile.yaml': 'two\n'})
        temporary = self.directory / 'tmp'
        temporary.mkdir()
        with mock.patch.object(tempfile, 'tempdir', str(temporary)):
            tree = self.upstream.tree(self.repository.url, new, 'notes', LIMITS)
            subjects = self.upstream.subjects(self.repository.url, old, new, 'notes')
            fetched = sum(path.stat().st_size for path in temporary.rglob('*') if path.is_file())
        self.assertEqual(tree, {'profile.yaml': b'two\n'})
        self.assertEqual([line.split(' ', 1)[1] for line in subjects], ['Change notes'])
        self.assertLess(fetched, 2**20)

    def test_a_fetch_past_the_limits_stops_before_its_bytes_land(self):
        (self.repository.work / 'notes').mkdir()
        (self.repository.work / 'notes/large.bin').write_bytes(random.Random(0).randbytes(8 * 2**20))  # incompressible
        commit = self.repository.commit('Add notes with a large file', {'notes/profile.yaml': 'one\n'})
        # A host that filters sends a folder's files in a fetch of their own; one that does not, in the clone.
        for host, filters in (('filters', 'true'), ('does not filter', 'false')):
            git('-C', self.repository.served, 'config', 'uploadpack.allowFilter', filters)
            temporary = self.directory / f'tmp-{filters}'
            temporary.mkdir()
            with (
                self.subTest(host),
                pins.Upstream() as upstream,
                mock.patch.object(tempfile, 'tempdir', str(temporary)),
                mock.patch.object(pins, 'REPOSITORY_BYTES', 2**20, create=True),
            ):
                with self.assertRaises(ValueError) as caught:
                    upstream.tree(self.repository.url, commit, 'notes', (200, 2**20))
                written = sum(path.stat().st_size for path in temporary.rglob('*') if path.is_file())
                self.assertLess(written, 3 * 2**20)
                self.assertIn('no file it writes may exceed', str(caught.exception))

    def test_a_malformed_object_is_refused(self):
        self.repository.commit('Add notes', {'notes/profile.yaml': 'one\n'})
        served = self.repository.served
        tree = git('-C', served, 'rev-parse', 'HEAD^{tree}')
        text = f'tree {tree}\nauthor A <a@example.com> 1 +0000\ncommitter A <a@example.com> never +0000\n\nBad date\n'
        commit = git('-C', served, 'hash-object', '--literally', '-w', '-t', 'commit', '--stdin', stdin=text)
        git('-C', served, 'update-ref', 'refs/heads/malformed', commit)
        with self.assertRaisesRegex(ValueError, 'git clone failed'):
            self.upstream.tree(self.repository.url, commit, 'notes', LIMITS)

    def test_more_files_or_bytes_than_the_limits_are_refused(self):
        commit = self.repository.commit('Add notes', {'notes/profile.yaml': 'one\n', 'notes/a.txt': 'a' * 20})
        with self.assertRaisesRegex(ValueError, '2 files, more than the 1 a profile may hold'):
            self.upstream.tree(self.repository.url, commit, 'notes', (1, 1000))
        with self.assertRaisesRegex(ValueError, '24 bytes, more than the 10 a profile may hold'):
            self.upstream.tree(self.repository.url, commit, 'notes', (10, 10))

    def test_subjects_name_the_commits_that_touched_the_folder_newest_first(self):
        old = self.repository.commit('Add notes', {'notes/profile.yaml': 'one\n'})
        self.repository.commit('Add a citation style', {'notes/a.csl': 'a\n'})
        self.repository.commit('Change the keys profile only', {'keys/profile.yaml': 'k\n'})
        new = self.repository.commit('Drop the citation style', remove=['notes/a.csl'])
        subjects = self.upstream.subjects(self.repository.url, old, new, 'notes')
        self.assertEqual(
            [line.split(' ', 1)[1] for line in subjects], ['Drop the citation style', 'Add a citation style']
        )
        self.assertTrue(all(new.startswith(line.split(' ')[0]) for line in subjects[:1]))

    def test_subjects_lose_control_characters(self):
        old = self.repository.commit('Add notes', {'notes/profile.yaml': 'one\n'})
        new = self.repository.commit('Add \x1b[31mred\x1b[0m and \u202ereversed\u202c text', {'notes/a.csl': 'a\n'})
        [subject] = self.upstream.subjects(self.repository.url, old, new, 'notes')
        self.assertEqual(subject.split(' ', 1)[1], 'Add [31mred[0m and reversed text')

    def test_subjects_say_when_the_author_rewrote_the_history(self):
        base = self.repository.commit('Add notes', {'notes/profile.yaml': 'one\n'})
        old = self.repository.commit('Change notes', {'notes/profile.yaml': 'two\n'})
        self.repository.rewind(base)
        new = self.repository.commit('Change notes differently', {'notes/profile.yaml': 'three\n'})
        self.assertIsNone(self.upstream.subjects(self.repository.url, old, new, 'notes'))

    def test_close_deletes_the_fetched_repositories(self):
        commit = self.repository.commit('Add notes', {'notes/profile.yaml': 'one\n'})
        temporary = self.directory / 'tmp'
        temporary.mkdir()
        with mock.patch.object(tempfile, 'tempdir', str(temporary)):
            self.upstream.tree(self.repository.url, commit, 'notes', LIMITS)
            self.assertTrue(any(temporary.iterdir()))
            self.upstream.close()
        self.assertEqual(list(temporary.iterdir()), [])


def tree_of(folder, skip=()):
    """{path: SHA-256, or 'dir'} of everything under FOLDER except SKIP's top-level names."""
    found = {}
    for path in sorted(Path(folder).rglob('*')):
        relative = path.relative_to(folder)
        if relative.parts[0] in skip:
            continue
        found[relative.as_posix()] = 'dir' if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
    return found


class CommandCase(GitCase):
    """A synthetic supplement, a Git repository of its own, and an author's repository at SOURCE."""

    def setUp(self):
        super().setUp()
        served = {
            'GIT_CONFIG_COUNT': '1',
            'GIT_CONFIG_KEY_0': f'url.{self.repository.url}.insteadOf',
            'GIT_CONFIG_VALUE_0': SOURCE,
        }
        patcher = mock.patch.dict(os.environ, served)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.supplement = self.directory / 'supplement'
        self.supplement.mkdir()
        self.config('')
        git('-C', self.supplement, 'init', '-q')
        git('-C', self.supplement, 'add', '-A')
        git('-C', self.supplement, 'commit', '-q', '-m', 'Start')
        self.first = self.repository.commit('Add notes and keys', NOTES)

    def config(self, extra):
        (self.supplement / 'config.yaml').write_text(f'schema_version: 1\nsupplement_id: synthetic\n{extra}')

    def add(self, *names, ref='HEAD'):
        return vendor.add(self.supplement, ROOT, SOURCE, names, ref, self.upstream)

    def update(self, *ids, ref=None):
        return vendor.update(self.supplement, ROOT, ids, ref, self.upstream)

    def remove(self, *ids, force=False):
        return vendor.remove(self.supplement, ROOT, ids, force)

    def load(self, *listed):
        return loader.load(ROOT, supplement=self.supplement, listed=list(listed))

    def lock(self):
        return json.loads((self.supplement / profiles.LOCK).read_text())['profiles']

    def vendored(self, profile_id='alice/notes'):
        return tree_of(self.supplement / profiles.FOLDER / profile_id)


class SourceTests(unittest.TestCase):
    def test_a_source_is_owner_slash_repo_or_an_https_url(self):
        self.assertEqual(vendor.source_url('Alice/profiles'), ('https://github.com/Alice/profiles', 'alice'))
        self.assertEqual(
            vendor.source_url('https://codeberg.org/Alice/profiles/'), ('https://codeberg.org/Alice/profiles', 'alice')
        )
        for text in (
            'git@example.test:alice/profiles',
            'http://example.test/alice/profiles',
            'https://alice:token@example.test/alice/profiles',
            'https://example.test/profiles',
            'profiles',
        ):
            with self.subTest(text=text), self.assertRaisesRegex(vendor.ProfileError, 'not a profile source'):
                vendor.source_url(text)

    def test_every_fragment_a_profile_may_set_says_why_it_runs_code(self):
        schema = json.loads((ROOT / 'schema/profile.schema.json').read_text())
        for tool in schema['properties']['overrides']['properties']:
            with self.subTest(tool=tool):
                self.assertIn(('overrides', tool), vendor.RUNS)


class AddTests(CommandCase):
    def test_add_vendors_what_the_profile_names_and_records_it(self):
        self.add('notes')
        self.assertEqual(
            sorted(self.vendored()), ['README.md', 'profile.yaml', 'shell', 'shell/notes.fish', 'style.csl']
        )  # the author's tests/ is not the profile's
        entry = self.lock()['alice/notes']
        self.assertEqual(
            (entry['source'], entry['path'], entry['ref'], entry['commit']), (SOURCE, 'notes', 'HEAD', self.first)
        )
        self.assertEqual(entry['files']['shell/notes.fish'], hashlib.sha256(b'set -g example_notes 1\n').hexdigest())
        assets = self.load('alice/notes')['shaffy']['assets']
        self.assertEqual(
            {asset['target'] for asset in assets},
            {'.config/fish/profiles/01-alice-notes.fish', '.local/share/example-notes/style.csl'},
        )

    def test_the_review_lists_what_runs_code_first_and_points_upstream(self):
        lines = self.add('notes').lines()
        self.assertEqual(lines[0], f'Vendored alice/notes at {self.first[:7]} (HEAD of {SOURCE})')
        self.assertEqual(lines[2], '  Synthetic notes for the tests.')
        running = lines.index('Runs code or reaches the network; read these first:')
        self.assertRegex(lines[running + 1], r'^  \+ overrides\.fish shell/notes\.fish +runs in every shell$')
        self.assertRegex(
            lines[running + 2], r'^  \+ assets ~/.local/share/example-notes/style.csl +a program reads it there'
        )
        self.assertNotIn('Other entries:', lines)
        text = '\n'.join(lines)
        self.assertIn('list "- alice/notes" under\nprofiles: in config.yaml', text)
        self.assertIn(f'./bin/shaffy profile propose alice/notes drafts it for the author:\n  {ISSUES}', text)
        self.assertIn('./bin/shaffy profile propose alice/notes --to shaffy', text)
        self.assertIn('With alice/notes listed in config.yaml, the settings load.', text)

    def test_an_asset_runs_code_unless_it_lies_where_only_data_goes(self):
        running = [
            '~/.bashrc',
            '~/.config/environment.d/90-example.conf',
            '~/.config/fish/functions/example.fish',
            '~/.config/git/config',
            '~/.config/mise/conf.d/50-example.toml',
        ]
        inert = [
            '~/.local/share/fonts/Example.otf',
            '~/.local/share/icons/example.svg',
            '~/.local/share/themes/Example/gtk-3.0/gtk.css',
            '~/Pictures/example.png',
        ]
        manifest = 'schema_version: 1\ndescription: Synthetic assets.\nassets:\n'
        manifest += ''.join(f'  "{target}": file\n' for target in running + inert)
        self.repository.commit('Add assets', {'mixed/profile.yaml': manifest, 'mixed/file': 'x\n'})
        lines = self.add('mixed').lines()
        first = lines.index('Runs code or reaches the network; read these first:')
        other = lines.index('Other entries:')

        def targets(group):
            return sorted(line.split()[2] for line in group if line.startswith('  + assets '))

        self.assertEqual(targets(lines[first:other]), running)
        self.assertEqual(targets(lines[other:]), inert)

    def test_the_review_loads_the_settings_with_the_profile_listed(self):
        self.repository.commit(
            'Add a plugin and a package',
            {
                'tools/profile.yaml': (
                    'schema_version: 1\ndescription: Synthetic tools.\npackages:\n  extra: [neovim]\n'
                    'ai:\n  claude_plugins:\n    example-two@example-plugins: other/plugins\n'
                ),
            },
        )
        self.config('ai:\n  claude_plugins:\n    example-one@example-plugins: example/plugins\n')
        text = '\n'.join(self.add('tools').lines())
        self.assertIn('With alice/tools listed in config.yaml, the settings do not load yet:', text)
        self.assertIn('the marketplace example-plugins is named with', text)
        self.config('')
        self.remove('alice/tools')
        text = '\n'.join(self.add('tools').lines())
        self.assertIn('the settings load, with these notes:', text)
        self.assertIn('neovim', text)
        self.assertIn('New Claude plugins (example-two@example-plugins): the workspace installer installs them', text)

    def test_add_twice_writes_nothing_new_and_says_update(self):
        self.add('notes')
        before = tree_of(self.supplement)
        with self.assertRaisesRegex(vendor.ProfileError, 'vendored already.*profile update alice/notes'):
            self.add('notes')
        self.assertEqual(tree_of(self.supplement), before)

    def test_add_refuses_an_id_locked_from_another_source(self):
        self.add('notes')
        before = tree_of(self.supplement)
        elsewhere = 'https://example.test/alice/elsewhere'
        with self.assertRaisesRegex(vendor.ProfileError, f'alice/notes is vendored already from notes/ in {SOURCE}'):
            vendor.add(self.supplement, ROOT, elsewhere, ['notes'], 'HEAD', self.upstream)
        self.assertEqual(tree_of(self.supplement), before)

    def test_add_refuses_what_a_load_would_refuse_and_writes_nothing(self):
        cases = {
            'a missing reference': (
                {'bad/profile.yaml': 'schema_version: 1\ndescription: x\noverrides:\n  fish: gone.fish\n'},
                "'gone.fish' does not exist in the profile",
            ),
            'a key a profile may not set': (
                {'bad/profile.yaml': 'schema_version: 1\ndescription: x\ndesktop:\n  browser: example\n'},
                'desktop.browser: a profile cannot set this.*--to shaffy --key desktop.browser',
            ),
            'a keyboard map that names one unit': (
                {
                    'bad/profile.yaml': 'schema_version: 1\ndescription: x\noverrides:\n  keyboard_hwdb: k.hwdb\n',
                    'bad/k.hwdb': (
                        'evdev:input:b0003v1234p5678*\n KEYBOARD_KEY_70039=leftmeta\n'
                        'evdev:phys:usb-0000:00:14.0-1/input0*\n'
                    ),
                },
                'bad/k.hwdb:3: this match names one unit',
            ),
            'no profile.yaml': ({'bad/notes.txt': 'x\n'}, 'bad/ in .* has no profile.yaml'),
        }
        for case, (files, message) in cases.items():
            with self.subTest(case):
                self.repository.commit(case, files)
                with self.assertRaisesRegex(vendor.ProfileError, message) as caught:
                    self.add('bad')
                if case != 'no profile.yaml':
                    self.assertIn(f'Tell the author: {ISSUES}', str(caught.exception))
                self.assertFalse((self.supplement / profiles.FOLDER).exists())
                self.repository.commit(f'Drop {case}', remove=list(files))

    def test_a_pinned_commit_on_a_host_that_refuses_it_by_hash_is_vendored(self):
        self.repository.commit('Change notes', {'notes/style.csl': '<style version="2"/>\n'})
        refusing = {
            'GIT_CONFIG_COUNT': '2',
            'GIT_CONFIG_KEY_1': 'protocol.version',
            'GIT_CONFIG_VALUE_1': '0',
        }
        with mock.patch.dict(os.environ, refusing):
            self.add('notes', ref=self.first)
        self.assertEqual(self.lock()['alice/notes']['ref'], self.first)
        self.assertEqual((self.supplement / 'profiles/alice/notes/style.csl').read_text(), '<style/>\n')


class UpdateTests(CommandCase):
    def test_update_prints_the_authors_subjects_between_the_two_commits(self):
        self.add('notes')
        self.repository.commit('Change the style', {'notes/style.csl': '<style version="2"/>\n'})
        self.repository.commit('Change the keys only', {'keys/profile.yaml': NOTES['keys/profile.yaml'] + '# x\n'})
        for number in range(3):
            self.repository.commit(f'Note {number}', {'notes/README.md': f'Synthetic notes {number}.\n'})
        with mock.patch.object(vendor, 'SUBJECTS', 3):
            lines = self.update('alice/notes').lines()
        start = lines.index("The author's commits to notes/ in between, newest first:")
        self.assertEqual(
            [line.split(' ', 3)[-1] for line in lines[start + 1 : start + 4]], ['Note 2', 'Note 1', 'Note 0']
        )
        self.assertEqual(lines[start + 4], '  and 1 more')
        self.assertRegex(
            lines[0], rf'^alice/notes: {self.first[:7]} → [0-9a-f]{{7}} \(HEAD of {SOURCE}\), 2 files changed\.$'
        )
        self.assertIn(
            f'Something wrong in it? ./bin/shaffy profile propose alice/notes drafts it for the author: {ISSUES}', lines
        )

    def test_update_says_when_the_author_rewrote_history(self):
        self.add('notes')
        self.repository.commit('Change the style', {'notes/style.csl': '<style version="2"/>\n'})
        self.repository.rewind(self.first)
        git('-C', self.repository.work, 'commit', '-q', '--amend', '-m', 'Add notes and keys, again')
        git('-C', self.repository.work, 'push', '-q', '--force', self.repository.served, 'main')
        lines = self.update().lines()
        self.assertIn(
            f'The author rewrote the history since {self.first[:7]}: read the whole change with '
            'git diff -- profiles/alice/notes.',
            lines,
        )

    def test_update_lists_new_code_running_entries_first(self):
        self.add('notes')
        manifest = (
            NOTES['notes/profile.yaml']
            .replace('assets:\n', 'assets:\n  "~/.local/bin/example-notes": {file: bin/notes, executable: true}\n')
            .replace('  "~/.local/share/example-notes/style.csl": style.csl\n', '')
        )
        manifest += (
            'ai:\n  mcp_servers:\n    example-notes:\n      command: example-notes-mcp\n      args: [--read-only]\n'
        )
        self.repository.commit(
            'Add a program and a server, drop the style',
            {
                'notes/profile.yaml': manifest,
                'notes/bin/notes': '#!/bin/sh\n',
                'notes/shell/notes.fish': 'set -g x 2\n',
            },
        )
        lines = self.update().lines()
        running = lines.index('Runs code or reaches the network; read these first:')
        other = lines.index('Other changes:')
        self.assertEqual(
            [line.split()[0:2] for line in lines[running + 1 : other]],
            [['+', 'assets'], ['+', 'ai.mcp_servers.example-notes'], ['~', 'overrides.fish']],
        )  # added, then changed; what leaves runs nothing more
        self.assertIn('an installed program', lines[running + 1])
        self.assertIn('the AI clients start: example-notes-mcp --read-only', lines[running + 2])
        self.assertRegex(
            lines[other + 1], r'^  - assets ~/.local/share/example-notes/style.csl +stays in the home, unmanaged'
        )

    def test_update_that_adds_a_mise_tool_names_the_workspace_installer(self):
        self.add('notes')
        manifest = NOTES['notes/profile.yaml'].replace(
            '  fish: shell/notes.fish\n', '  fish: shell/notes.fish\n  mise: tools.toml\n'
        )
        self.repository.commit(
            'Add a mise tool', {'notes/profile.yaml': manifest, 'notes/tools.toml': '[tools]\nexample-tool = "1"\n'}
        )
        text = '\n'.join(self.update().lines())
        self.assertIn(
            'New mise tools (example-tool): the workspace installer installs them; ./bin/shaffy update runs it', text
        )

    def test_update_refuses_hand_edits_and_names_propose(self):
        self.add('notes', 'keys')
        self.repository.commit(
            'Change both',
            {'notes/style.csl': '<style version="2"/>\n', 'keys/profile.yaml': NOTES['keys/profile.yaml'] + '# 2\n'},
        )
        edited = self.supplement / 'profiles/alice/notes/style.csl'
        edited.write_text('<style mine="yes"/>\n')
        outcome = self.update()
        [refusal] = outcome.refused
        self.assertIn('alice/notes: its copy has hand edits', refusal)
        self.assertIn('./bin/shaffy profile propose alice/notes drafts them for the author', refusal)
        self.assertEqual(edited.read_text(), '<style mine="yes"/>\n')
        self.assertEqual(self.lock()['alice/notes']['commit'], self.first)
        self.assertEqual([review.after.id for review in outcome.reviews], ['alice/keys'])
        self.assertNotEqual(self.lock()['alice/keys']['commit'], self.first)

    def test_an_upstream_commit_that_does_not_load_keeps_the_copy(self):
        self.add('notes')
        before = tree_of(self.supplement)
        self.repository.commit(
            'Set the browser', {'notes/profile.yaml': NOTES['notes/profile.yaml'] + 'desktop:\n  browser: x\n'}
        )
        outcome = self.update()
        [refusal] = outcome.refused
        self.assertRegex(
            refusal,
            f'alice/notes at [0-9a-f]{{7}} does not load here. alice/notes stays as it is. Tell the author: {ISSUES}',
        )
        self.assertIn('desktop.browser: a profile cannot set this', refusal)
        self.assertEqual(tree_of(self.supplement), before)

    def test_update_twice_says_up_to_date_and_writes_nothing(self):
        self.add('notes')
        self.repository.commit('Change the style', {'notes/style.csl': '<style version="2"/>\n'})
        self.update()
        after = tree_of(self.supplement)
        lines = self.update().lines()
        self.assertEqual(
            lines[0], f'alice/notes: up to date at {self.lock()["alice/notes"]["commit"][:7]} (HEAD of {SOURCE}).'
        )
        self.assertEqual(tree_of(self.supplement), after)

    def test_a_source_that_cannot_be_read_keeps_its_profile_and_the_others_reach_the_lock(self):
        other = Repository(self.directory / 'bob')
        bob = 'https://example.test/bob/profiles'
        served = {'GIT_CONFIG_COUNT': '2', 'GIT_CONFIG_KEY_1': f'url.{other.url}.insteadOf', 'GIT_CONFIG_VALUE_1': bob}
        patcher = mock.patch.dict(os.environ, served)
        patcher.start()
        self.addCleanup(patcher.stop)
        other.commit('Add tools', {'tools/profile.yaml': NOTES['keys/profile.yaml']})
        self.add('notes')
        vendor.add(self.supplement, ROOT, bob, ['tools'], 'HEAD', self.upstream)
        newer = self.repository.commit('Change the style', {'notes/style.csl': '<style version="2"/>\n'})
        tools = other.commit('Describe tools', {'tools/README.md': 'Synthetic tools.\n'})
        before = self.vendored('bob/tools')
        real = pins._git

        def unreachable(repository, *arguments, **keywords):
            if arguments[0] == 'clone' and bob in arguments:
                raise ValueError('git clone failed: a synthetic outage')
            return real(repository, *arguments, **keywords)

        with mock.patch.object(pins, '_git', unreachable):
            outcome = self.update()
        [refusal] = outcome.refused
        self.assertRegex(refusal, '^bob/tools: git clone failed: a synthetic outage. It stays as it is')
        self.assertEqual(self.lock()['alice/notes']['commit'], newer)
        self.assertEqual(self.vendored('bob/tools'), before)
        self.load('alice/notes', 'bob/tools')  # each folder matches its lock entry
        self.update()
        self.assertEqual(self.lock()['bob/tools']['commit'], tools)
        self.load('alice/notes', 'bob/tools')


class RemoveTests(CommandCase):
    def test_remove_refuses_while_listed_and_names_what_stays(self):
        self.repository.commit(
            'Add a package',
            {'notes/profile.yaml': NOTES['notes/profile.yaml'] + 'packages:\n  extra: [example-notes]\n'},
        )
        self.add('notes')
        self.config('profiles: [alice/notes]\n')
        with self.assertRaisesRegex(vendor.ProfileError, 'config.yaml still lists alice/notes'):
            self.remove('alice/notes')
        self.assertTrue((self.supplement / 'profiles/alice/notes').exists())
        self.config('')
        lines = self.remove('alice/notes')
        self.assertIn(
            '  assets: ~/.local/share/example-notes/style.csl stays in the home, unmanaged, after the next apply', lines
        )
        self.assertIn(
            '  packages.extra: example-notes stays installed; list it in packages.remove to uninstall it', lines
        )
        self.assertEqual(
            lines[-2:], ['Removed it because something was wrong or missing? Tell the author:', f'  {ISSUES}']
        )
        self.assertFalse((self.supplement / profiles.FOLDER).exists())  # the last one: no folder, no lock
        self.assertEqual(self.remove('alice/notes'), ['alice/notes: not vendored here; nothing to remove.'])

    def test_remove_refuses_hand_edits_and_a_folder_without_a_lock_entry(self):
        self.add('notes')
        edited = self.supplement / 'profiles/alice/notes/style.csl'
        edited.write_text('<style mine="yes"/>\n')
        (edited.parent / 'mine.csl').write_text('<style/>\n')
        (edited.parent / 'README.md').unlink()  # a deletion loses nothing
        with self.assertRaisesRegex(
            vendor.ProfileError,
            r'^alice/notes: its copy has hand edits \(mine.csl, style.csl\), which remove would delete\. '
            r'.*profile propose alice/notes.* commit .*profile remove --force alice/notes',
        ):
            self.remove('alice/notes')
        own = self.supplement / 'profiles/bob/mine'
        own.mkdir(parents=True)
        (own / 'profile.yaml').write_text('schema_version: 1\ndescription: Mine.\n')
        with self.assertRaisesRegex(
            vendor.ProfileError, r'^profiles/bob/mine has no lock entry.* Commit them .*profile remove --force bob/mine'
        ):
            self.remove('bob/mine')
        self.assertEqual(edited.read_text(), '<style mine="yes"/>\n')
        self.assertTrue((own / 'profile.yaml').exists())
        self.assertIn('alice/notes', self.lock())
        lines = self.remove('alice/notes', 'bob/mine', force=True)
        self.assertIn('Removed profiles/bob/mine, which had no lock entry, so nothing read it.', lines)
        self.assertFalse((self.supplement / profiles.FOLDER).exists())

    def test_remove_and_update_take_a_copy_whose_profile_yaml_no_longer_loads(self):
        older = (
            'schema_version: 1\ndescription: x\noverrides:\n  example: x.conf\n'
            'assets:\n  "~/.local/share/x": {executable: true}\n'
        )
        for profile_id in ('alice/notes', 'alice/old'):
            vendor_profile(self.supplement, profile_id, {'profile.yaml': older, 'x.conf': 'x\n'}, source=SOURCE)
        text = '\n'.join(self.update('alice/notes').lines())
        self.assertIn('+ overrides.fish', text)  # what the copy set is unknown, so every entry is new
        lines = self.remove('alice/old')
        self.assertIn(
            'What stays on the machine is not listed: profiles/alice/old/profile.yaml is missing or does not load '
            'here (docs/PROFILES.md#removing-a-profile).',
            lines,
        )
        self.assertFalse((self.supplement / 'profiles/alice/old').exists())

    def test_remove_force_takes_a_copy_whose_profile_yaml_cannot_be_read(self):
        for case, content in (
            ('not UTF-8', b'schema_version: 1\ndescription: \xff\n'),
            ('nested too deeply', b'schema_version: 1\ndescription: ' + b'[' * 2000 + b']' * 2000),
            ('a list as a key', b'schema_version: 1\n? [a]\n: b\n'),
            ('a number too long', b'schema_version: ' + b'9' * 5000),
        ):
            with self.subTest(case):
                self.add('notes')
                (self.supplement / 'profiles/alice/notes/profile.yaml').write_bytes(content)
                lines = self.remove('alice/notes', force=True)
                self.assertIn(
                    'Removed profiles/alice/notes and its lock entry; review with git diff and commit.', lines
                )
                self.assertFalse((self.supplement / profiles.FOLDER).exists())  # the folder and the lock


class CrashTests(CommandCase):
    """Every crash point of the design's table, and the rerun that converges."""

    def interrupted(self, target, side_effect, command):
        with mock.patch.object(*target, side_effect=side_effect), self.assertRaises(RuntimeError):
            command()

    def newer(self):
        return self.repository.commit('Change the style', {'notes/style.csl': '<style version="2"/>\n'})

    def test_before_the_swap_a_staging_folder_stays_until_the_next_command(self):
        self.interrupted((vendor, '_swap'), RuntimeError('crash'), lambda: self.add('notes'))
        staging = [path.name for path in (self.supplement / profiles.FOLDER).iterdir()]
        self.assertEqual(len(staging), 1)
        self.assertTrue(staging[0].startswith('.staging-alice-notes-'))
        self.add('notes')
        self.assertEqual(
            sorted(path.name for path in (self.supplement / profiles.FOLDER).iterdir()), ['alice', 'lock.json']
        )

    def test_between_the_two_renames_the_rerun_converges(self):
        self.add('notes')
        newer = self.newer()
        calls = []

        def rename(source, target):
            calls.append(source)
            if len(calls) == 2:
                raise RuntimeError('crash')
            os.replace(source, target)

        self.interrupted((vendor.os, 'rename'), rename, lambda: self.update())
        self.assertFalse((self.supplement / 'profiles/alice/notes').exists())
        with self.assertRaises(loader.SettingsError) as caught:
            self.load('alice/notes')
        self.assertIn(
            'missing; run ./bin/shaffy profile update alice/notes or git restore profiles/',
            caught.exception.problems[0],
        )
        self.update()
        self.assertEqual(self.lock()['alice/notes']['commit'], newer)
        self.load('alice/notes')

    def test_after_the_swap_before_the_lock_the_rerun_writes_the_lock(self):
        self.add('notes')
        newer = self.newer()
        self.interrupted((vendor, '_write_lock'), RuntimeError('crash'), lambda: self.update())
        self.assertEqual((self.supplement / 'profiles/alice/notes/style.csl').read_text(), '<style version="2"/>\n')
        with self.assertRaises(loader.SettingsError) as caught:
            self.load('alice/notes')
        self.assertIn('If a profile command was interrupted, run it again.', caught.exception.problems[-1])
        vendored = self.vendored()
        self.update()
        self.assertEqual(self.lock()['alice/notes']['commit'], newer)
        self.assertEqual(self.vendored(), vendored)
        self.load('alice/notes')

    def test_an_add_interrupted_before_the_lock_is_finished_by_the_rerun(self):
        self.interrupted((vendor, '_write_lock'), RuntimeError('crash'), lambda: self.add('notes'))
        self.assertFalse((self.supplement / profiles.LOCK).exists())
        vendored = self.vendored()
        self.add('notes')
        self.assertEqual(self.vendored(), vendored)
        self.assertEqual(self.lock()['alice/notes']['commit'], self.first)

    def test_a_crash_in_one_profile_keeps_the_lock_entries_of_those_before_it(self):
        self.add('keys', 'notes')
        newer = self.repository.commit(
            'Change both',
            {'keys/profile.yaml': NOTES['keys/profile.yaml'] + '# 2\n', 'notes/style.csl': '<style version="2"/>\n'},
        )
        real = vendor._swap

        def swap(folder, profile_id, staged):
            if profile_id == 'alice/notes':
                raise RuntimeError('crash')
            real(folder, profile_id, staged)

        self.interrupted((vendor, '_swap'), swap, lambda: self.update())
        self.assertEqual(self.lock()['alice/keys']['commit'], newer)
        self.load('alice/keys')
        self.update()
        self.load('alice/keys', 'alice/notes')

    def test_inside_remove_the_rerun_deletes_what_is_left(self):
        self.add('notes', 'keys')
        real = vendor.shutil.rmtree

        def rmtree(path, *arguments, **keywords):
            if Path(path).name == 'notes':
                (Path(path) / 'style.csl').unlink()  # partway through
                raise RuntimeError('crash')
            real(path, *arguments, **keywords)

        self.interrupted((vendor.shutil, 'rmtree'), rmtree, lambda: self.remove('alice/notes'))
        self.assertIn('alice/notes', self.lock())  # written last
        self.assertTrue((self.supplement / 'profiles/alice/notes').exists())
        self.remove('alice/notes')  # what is left holds no hand edit
        self.assertFalse((self.supplement / 'profiles/alice/notes').exists())
        self.assertEqual(list(self.lock()), ['alice/keys'])

    def test_a_second_command_while_one_runs_is_refused(self):
        with vendor._exclusive(self.supplement) as folder:
            with self.assertRaisesRegex(vendor.ProfileError, 'another profile command is changing'):
                self.add('notes')
            self.assertEqual(list(folder.iterdir()), [])
            folder.rmdir()  # as the first command does at its end when it leaves the folder empty
            with self.assertRaisesRegex(vendor.ProfileError, 'another profile command is changing'):
                self.add('notes')

    def test_every_kind_of_leftover_is_deleted_or_named(self):
        folder = self.supplement / profiles.FOLDER
        folder.mkdir()
        outside = self.directory / 'outside'
        outside.mkdir()
        (outside / 'kept').write_text('x\n')
        (folder / '.staging-link').symlink_to(outside)
        (folder / '.staging-file').write_text('x\n')
        (folder / '.lock-folder').mkdir()
        (folder / '.lock-folder/x').write_text('x\n')
        self.add('notes')
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ['alice', 'lock.json'])
        self.assertEqual((outside / 'kept').read_text(), 'x\n')
        (folder / '.staging-stuck').mkdir()
        refused = PermissionError(13, 'Permission denied')
        with (
            mock.patch.object(vendor.shutil, 'rmtree', side_effect=refused),
            self.assertRaisesRegex(
                vendor.ProfileError,
                r'staging-stuck: left by an interrupted profile command, and it cannot be deleted \(Permission '
                r'denied\); move it out of profiles/',
            ),
        ):
            self.update()


class BoundaryTests(CommandCase):
    def test_commands_write_nothing_outside_profiles(self):
        home = self.directory / 'home'
        home.mkdir()
        before = tree_of(self.supplement, skip={profiles.FOLDER})
        with mock.patch.dict(os.environ, {'HOME': str(home)}):
            self.add('notes', 'keys')
            self.repository.commit('Change the style', {'notes/style.csl': '<style version="2"/>\n'})
            self.update()
            self.remove('alice/keys')
        self.assertEqual(tree_of(self.supplement, skip={profiles.FOLDER}), before)  # config.yaml and .git included
        self.assertEqual(list(home.iterdir()), [])
        status = git('-C', self.supplement, 'status', '--porcelain', '--untracked-files=all')
        self.assertTrue(all(line[3:].startswith('profiles/') for line in status.splitlines()), status)

    def test_an_id_or_a_link_that_leads_out_of_profiles_is_refused(self):
        outside = self.directory / 'outside'
        (outside / 'notes').mkdir(parents=True)
        for profile_id in ('../outside', 'alice/../../outside', '/tmp/x'):
            with self.subTest(profile_id), self.assertRaisesRegex(vendor.ProfileError, 'is not a profile ID'):
                self.remove(profile_id)
        self.add('notes')
        os.symlink(outside, self.supplement / 'profiles/bob')
        with self.assertRaisesRegex(vendor.ProfileError, 'profiles/bob is a link'):
            self.remove('bob/notes')
        self.assertTrue((outside / 'notes').is_dir())

    def test_running_each_command_twice_gives_byte_identical_files(self):
        self.add('notes', 'keys')
        once = tree_of(self.supplement)
        with self.assertRaises(vendor.ProfileError):
            self.add('notes', 'keys')
        self.assertEqual(tree_of(self.supplement), once)
        self.repository.commit('Change the style', {'notes/style.csl': '<style version="2"/>\n'})
        self.update()
        once = tree_of(self.supplement)
        self.update()
        self.assertEqual(tree_of(self.supplement), once)
        self.remove('alice/keys')
        once = tree_of(self.supplement)
        self.remove('alice/keys')
        self.assertEqual(tree_of(self.supplement), once)

    def test_the_profile_command_prints_the_review_and_refuses_with_status_1(self):
        env = dict(os.environ, HOME=str(self.directory / 'home'), PATH='/usr/bin:/bin', LANG='C.UTF-8')
        shaffy = [str(ROOT / 'bin/shaffy'), 'profile']
        added = subprocess.run(
            [*shaffy, 'add', SOURCE, 'notes', '--supplement', str(self.supplement)],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertTrue(added.stdout.startswith(f'Vendored alice/notes at {self.first[:7]}'), added.stdout)
        again = subprocess.run(
            [*shaffy, 'add', SOURCE, 'notes', '--supplement', str(self.supplement)],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )
        self.assertEqual(again.returncode, 1)
        self.assertTrue(again.stderr.startswith('shaffy: alice/notes is vendored already'), again.stderr)

    def test_the_profile_command_shows_no_control_character_an_author_or_a_lock_holds(self):
        env = dict(os.environ, HOME=str(self.directory / 'home'), PATH='/usr/bin:/bin', LANG='C.UTF-8')
        planted = '\x1b]52;c;ZXhhbXBsZQ==\x07'

        def shaffy(*arguments):
            return subprocess.run(
                [str(ROOT / 'bin/shaffy'), 'profile', *arguments, '--supplement', str(self.supplement)],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
                check=False,
            )

        refused = shaffy('add', SOURCE, 'notes', '--ref', self.repository.craft('notes', {planted: '120000'}))
        self.add('keys')
        lock = json.loads((self.supplement / profiles.LOCK).read_text())
        lock['profiles']['alice/keys']['source'] = SOURCE + planted
        (self.supplement / profiles.LOCK).write_text(json.dumps(lock))
        unread = shaffy('update')
        for result, said in ((refused, 'a control or format character'), (unread, 'cannot be read')):
            with self.subTest(said):
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(said, result.stderr)
                shown = result.stdout + result.stderr
                self.assertEqual(shown, '\n'.join(pins.printable(line) for line in shown.split('\n')))


class ProposeTests(CommandCase):
    def edit(self):
        """Hand edits of every kind: a changed text file, a binary one, a new file and a deleted one."""
        folder = self.supplement / 'profiles/alice/notes'
        (folder / 'style.csl').write_text('<style version="mine"/>\n')
        (folder / 'shell/notes.fish').write_bytes(b'\x00\x01binary\xff')
        (folder / 'extra.csl').write_text('<style/>\n')
        (folder / 'README.md').unlink()
        return folder

    def test_propose_prints_a_patch_that_applies_to_the_source(self):
        self.add('notes')
        folder = self.edit()
        patch, header = sharing.propose(self.supplement, ROOT, 'alice/notes', self.upstream)
        self.assertEqual(header[0], f'A fix for alice/notes against {SOURCE} at {self.first[:7]}.')
        self.assertEqual(header[1], 'Personal values: none found in the added lines and the bytes of each binary file.')
        self.assertIn(
            'Left out of the patch, since the lock does not list it, and not scanned: notes/extra.csl.',
            '\n'.join(header),
        )
        self.assertEqual(
            header[-2:],
            [
                'Then restore your copy (git restore profiles/alice/notes) and keep the change in config.yaml '
                'meanwhile,',
                'with a comment naming the issue it waits for.',
            ],
        )
        clone = self.directory / 'clone'
        git('clone', '-q', self.repository.url, clone)
        git('-C', clone, 'checkout', '-q', self.first)
        (self.directory / 'fix.patch').write_text(patch, errors='surrogateescape')
        git('-C', clone, 'apply', '--check', self.directory / 'fix.patch')
        git('-C', clone, 'apply', self.directory / 'fix.patch')
        for name in ('style.csl', 'shell/notes.fish', 'profile.yaml'):
            self.assertEqual((clone / 'notes' / name).read_bytes(), (folder / name).read_bytes(), name)
        self.assertFalse((clone / 'notes/README.md').exists())
        self.assertFalse((clone / 'notes/extra.csl').exists())
        self.assertTrue((clone / 'notes/tests/check.sh').exists())  # never vendored, so never touched

    def test_propose_warns_of_personal_values_without_printing_them(self):
        self.add('notes')
        planted = '/home/' + 'example-person/notes'  # split, so the repository's own scan passes this file
        (self.supplement / 'profiles/alice/notes/style.csl').write_text(f'<style/>\n<!-- {planted} -->\n')
        _, header = sharing.propose(self.supplement, ROOT, 'alice/notes', self.upstream)
        self.assertIn('  notes/style.csl:2: home-path', header)
        self.assertTrue(header[1].startswith('Personal values: replace these in the added lines'))
        self.assertNotIn(planted, '\n'.join(header))

    def test_propose_scans_a_binary_files_bytes_and_leaves_out_what_the_lock_does_not_list(self):
        self.add('notes')
        folder = self.supplement / 'profiles/alice/notes'
        planted = ('/home/' + 'example-person/photos').encode()  # split, so the repository's own scan passes
        (folder / 'style.csl').write_bytes(b'\xff\xd8\x00Exif\x00' + planted + b'\x00\xff')
        (folder / 'photo.jpg').write_bytes(b'\xff\xd8\x00' + planted)
        patch, header = sharing.propose(self.supplement, ROOT, 'alice/notes', self.upstream)
        self.assertIn('diff --git a/notes/style.csl b/notes/style.csl', patch)
        self.assertNotIn('photo.jpg', patch)
        self.assertIn('  notes/style.csl (its bytes): home-path', header)
        text = '\n'.join(header)
        self.assertIn('Binary, so scanned as bytes only: notes/style.csl.', text)
        self.assertIn('Left out of the patch, since the lock does not list it, and not scanned: notes/photo.jpg.', text)
        self.assertNotIn(planted.decode(), text)

    def test_the_supplements_denylist_and_identifying_settings_are_scanned_too(self):
        self.add('notes')
        self.config('profiles: [alice/notes]\nidentity:\n  name: Example Person\n')
        (self.supplement / 'denylist.txt').write_text('# synthetic\n\\bexample-secret-word\\b\n\\balice\\b\n')
        (self.supplement / 'profiles/alice/notes/style.csl').write_text(
            '<style/>\n<!-- Example Person -->\n<!-- example-secret-word -->\n'
        )
        _, header = sharing.propose(self.supplement, ROOT, 'alice/notes', self.upstream)
        self.assertIn('  notes/style.csl:2: settings field identity.name', header)
        self.assertIn('  notes/style.csl:3: denylist.txt line 2', header)
        self.assertNotIn('Example Person', '\n'.join(header))
        draft = sharing.propose_shaffy(self.supplement, ROOT, 'alice/notes')
        self.assertEqual(draft[2], 'Personal values: replace what matches denylist.txt line 3 first.')  # the source
        (self.supplement / 'denylist.txt').write_text('(\n')
        with self.assertRaisesRegex(vendor.ProfileError, 'denylist.txt line 1 is not a valid regular expression'):
            sharing.propose_shaffy(self.supplement, ROOT, 'alice/notes')

    def test_propose_without_edits_has_nothing_to_propose(self):
        self.add('notes')
        with self.assertRaisesRegex(vendor.ProfileError, f'nothing to propose: .* as vendored at {self.first[:7]}'):
            sharing.propose(self.supplement, ROOT, 'alice/notes', self.upstream)

    def test_propose_to_a_github_author_links_the_fork_and_a_prefilled_issue(self):
        self.add('notes')
        self.edit()
        lock = json.loads((self.supplement / profiles.LOCK).read_text())
        lock['profiles']['alice/notes']['source'] = 'https://github.com/alice/profiles'
        (self.supplement / profiles.LOCK).write_text(json.dumps(lock))
        with mock.patch.dict(os.environ, {'GIT_CONFIG_VALUE_0': 'https://github.com/alice/profiles'}):
            _, header = sharing.propose(self.supplement, ROOT, 'alice/notes', self.upstream)
        text = '\n'.join(header)
        self.assertIn('fork the repository (https://github.com/alice/profiles/fork)', text)
        body = f'A fix for the notes profile at {self.first[:7]}.\n\n'
        body += '<attach the patch here, and say what it changes and why>'
        self.assertIn(f'https://github.com/alice/profiles/issues/new?title=notes%3A%20&body={quote(body)}', text)

    def test_propose_scans_every_added_line_whatever_it_holds(self):
        self.add('notes')
        planted = '/home/' + 'example-person/notes'  # split, so the repository's own scan passes this file
        # A vertical tab or a line separator is no line end in a patch; an added "++ " line is no header.
        (self.supplement / 'profiles/alice/notes/style.csl').write_text(
            f'<style/>\n<!-- a\x0b{planted} -->\n++ {planted}\n<!-- \u2028{planted} -->\n'
        )
        _, header = sharing.propose(self.supplement, ROOT, 'alice/notes', self.upstream)
        self.assertEqual(
            [line for line in header if line.startswith('  notes/')],
            ['  notes/style.csl:2: home-path', '  notes/style.csl:3: home-path', '  notes/style.csl:4: home-path'],
        )

    def test_propose_says_when_the_patch_alone_breaks_the_profile(self):
        self.add('notes')
        folder = self.supplement / 'profiles/alice/notes'
        extra = '  "~/.local/share/example-notes/extra.csl": extra.csl\n'
        (folder / 'profile.yaml').write_text(NOTES['notes/profile.yaml'] + extra)
        (folder / 'extra.csl').write_text('<style/>\n')
        _, header = sharing.propose(self.supplement, ROOT, 'alice/notes', self.upstream)
        self.assertIn(
            'Your profile.yaml names notes/extra.csl, which the patch leaves out, so the patch alone breaks the '
            'profile: send that file with it.',
            header,
        )

    def test_propose_to_shaffy_drafts_the_configuration_form(self):
        self.add('notes')
        self.config('profiles: [alice/notes]\n')
        draft = sharing.propose_shaffy(self.supplement, ROOT, 'alice/notes', 'desktop.keyboard')
        form = loader.parse(ROOT / sharing.FORM)
        fields = {field['id']: field['attributes'] for field in form['body'] if 'id' in field}
        self.assertIn(f'{sharing.SHAFFY_ISSUES}/new?template=configuration.yml', draft[1])
        self.assertEqual(draft[2], 'Personal values: none found in the draft.')
        body = draft[draft.index('') + 1 :]
        self.assertEqual(body[0], 'Title: Profiles: let a profile set desktop.keyboard')
        self.assertEqual(body[1], f'{fields["configuration"]["label"]}:')
        self.assertTrue(body[2].startswith(f'  # Profile alice/notes at {self.first[:7]} ({SOURCE}), Shaffy OS '))
        self.assertIn('the layouts a person types are theirs', ' '.join(body))
        self.assertEqual(body[-4:-2], ['  desktop:', '    keyboard: <what the profile would set>'])
        where = body[-2].removeprefix(f'{fields["where"]["label"]}: ')
        self.assertIn(where, fields['where']['options'])
        self.assertTrue(body[-1].startswith(f'{fields["why"]["label"]}: '))
        text = '\n'.join(draft)
        for personal in (str(self.supplement), 'synthetic', str(self.directory)):
            self.assertNotIn(personal, text)

    def test_propose_to_shaffy_needs_no_profile_and_refuses_a_key_profiles_may_set(self):
        draft = sharing.propose_shaffy(None, ROOT)
        self.assertIn('Title: Profiles: <the mechanism profiles need>', draft)
        with self.assertRaisesRegex(vendor.ProfileError, 'assets is not a setting profiles are refused'):
            sharing.propose_shaffy(None, ROOT, key='assets')

    def test_the_propose_command_writes_the_patch_to_stdout_and_the_rest_to_stderr(self):
        self.add('notes')
        self.edit()
        env = dict(os.environ, HOME=str(self.directory / 'home'), PATH='/usr/bin:/bin', LANG='C.UTF-8')
        result = subprocess.run(
            [str(ROOT / 'bin/shaffy'), 'profile', 'propose', 'alice/notes', '--supplement', str(self.supplement)],
            capture_output=True,
            env=env,
            timeout=120,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith(b'diff --git a/notes/'), result.stdout[:80])
        self.assertTrue(result.stderr.startswith(b'A fix for alice/notes against'), result.stderr)

    def test_the_propose_command_refuses_to_write_the_patch_to_a_terminal(self):
        self.add('notes')
        self.edit()
        env = dict(os.environ, HOME=str(self.directory / 'home'), PATH='/usr/bin:/bin', LANG='C.UTF-8')
        leader, follower = pty.openpty()
        self.addCleanup(os.close, leader)
        try:
            result = subprocess.run(
                [str(ROOT / 'bin/shaffy'), 'profile', 'propose', 'alice/notes', '--supplement', str(self.supplement)],
                stdout=follower,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                timeout=120,
                check=False,
            )
        finally:
            os.close(follower)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('redirect it into a file: ./bin/shaffy profile propose alice/notes > ', result.stderr)


class ReportTests(CommandCase):
    """The profile lines of check and status."""

    def test_check_notes_name_what_nothing_reads(self):
        self.add('notes', 'keys')
        self.config('profiles: [alice/notes]\n')
        folder = self.supplement / profiles.FOLDER
        (folder / 'alice/notes/stray.txt').write_text('x\n')
        (folder / 'bob/left').mkdir(parents=True)
        (folder / '.staging-alice-notes-x').mkdir()
        notes = sharing.findings(self.supplement, self.listed_data())
        self.assertIn(
            f'{folder}/.staging-alice-notes-x: left by an interrupted profile command; the next one deletes it', notes
        )
        self.assertIn(
            'profiles/alice/keys is vendored, but config.yaml does not list it, so nothing reads it: list it under '
            'profiles:, or ./bin/shaffy profile remove alice/keys',
            notes,
        )
        self.assertIn(
            'profiles/alice/notes/stray.txt: not in the lock, so nothing reads it; delete it, or ./bin/shaffy profile '
            'propose alice/notes drafts it for the author',
            notes,
        )
        self.assertIn(
            'profiles/bob/left has no lock entry, so nothing reads it: commit or move what you keep, then '
            './bin/shaffy profile remove --force bob/left deletes it',
            notes,
        )

    def listed_data(self):
        return loader.load(ROOT, supplement=self.supplement)['shaffy']

    def test_keys_and_keyboard_matches_two_profiles_both_set_are_noted_with_both_authors(self):
        manifest = 'schema_version: 1\ndescription: x\noverrides:\n  noctalia: shell.toml\n  keyboard_hwdb: keys.hwdb\n'
        match = 'evdev:input:b0003v1234p5678*\n'
        self.repository.commit(
            'Add two that overlap',
            {
                'one/profile.yaml': manifest,
                'one/shell.toml': '[osd]\nposition = "top_center"\nlocation = "x"\n',
                'one/keys.hwdb': match + ' KEYBOARD_KEY_70039=leftmeta\n',
                'two/profile.yaml': manifest,
                'two/shell.toml': '[osd]\nposition = "bottom_center"\nlocation = "x"\n',
                'two/keys.hwdb': match + ' KEYBOARD_KEY_70039=capslock\n',
            },
        )
        self.add('one', 'two')
        self.config('profiles: [alice/one, alice/two]\n')
        notes = sharing.findings(self.supplement, self.listed_data())
        self.assertEqual(
            notes,
            [
                f'keyboard map {match.strip()}: alice/one and alice/two both set it; alice/two, later in the list, '
                'wins where they differ. Set it in your own overrides.keyboard_hwdb fragment to choose, or tell the '
                f'authors: {ISSUES}',
                'noctalia osd.position: alice/one and alice/two both set it; alice/two, later in the list, wins where '
                f'they differ. Set it in your own noctalia fragment to choose, or tell the authors: {ISSUES}',
            ],
        )  # osd.location is the same in both: no note

    def test_status_lists_the_profiles_in_use_and_invites_sharing(self):
        self.add('notes')
        for name in ('own.fish', 'own.toml', 'font.otf'):
            (self.supplement / name).write_text('# mine\n')
        self.config(
            'profiles: [alice/notes]\noverrides:\n  fish: own.fish\n  mise: own.toml\n'
            'assets:\n  "~/.local/share/fonts/x.otf": font.otf\n'
            'lazyvim:\n  extras: [lazyvim.plugins.extras.lang.ruby]\n'
        )
        lines = sharing.summary(self.supplement, self.listed_data())
        self.assertEqual(
            lines,
            [
                'Profiles: 1 in use',
                f'  alice/notes at {self.first[:7]}, following HEAD of {SOURCE}',
                '  config.yaml also sets 1 asset, lazyvim.extras and overrides (fish, mise). A profile could share '
                'what names no person or machine: docs/PROFILES.md#share-yours, then ./bin/shaffy profile check',
            ],
        )

    def test_check_and_status_print_the_profile_lines(self):
        self.add('notes', 'keys')
        self.config('profiles: [alice/notes]\n')
        state = self.directory / 'state'
        env = {
            **os.environ,
            'HOME': str(self.directory / 'home'),
            'XDG_STATE_HOME': str(state),
            'PATH': '/usr/bin:/bin',
            'SHAFFY_TEST_PREREQUISITES': 'fake',
            'SHAFFY_TEST_ROOT_STATE': str(self.directory / 'root'),
        }

        def shaffy(*arguments):
            result = subprocess.run(
                [str(ROOT / 'bin/shaffy'), *arguments],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

        checked = shaffy('check', '--no-render', '--no-home', '--supplement', str(self.supplement))
        self.assertIn('Note: profiles/alice/keys is vendored, but config.yaml does not list it', checked)
        shaffy('supplement', 'attach', '--supplement', str(self.supplement))
        status = shaffy('status', '--no-drift').splitlines()
        start = status.index('Profiles: 1 in use')
        self.assertEqual(status[start + 1], f'  alice/notes at {self.first[:7]}, following HEAD of {SOURCE}')
        self.assertTrue(status[start + 2].startswith('  Note: profiles/alice/keys is vendored'), status)


if __name__ == '__main__':
    unittest.main()
