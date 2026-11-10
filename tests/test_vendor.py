"""The profile commands and their reads of a profile's repository, against local Git repositories.

Every repository is built in a temporary folder and served as a bare copy over file://, so no
test reaches the network, and every git reads no configuration of this machine's.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shaffy import loader, pins, profiles, vendor

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
            '.git': (self.repository.craft('notes', {'.git': '040000'}), "'.git/config' is not a name"),
            '.GIT': (self.repository.craft('notes', {'.GIT': '040000'}), "'.GIT/config' is not a name"),
            '..': (self.repository.craft('notes', {'..': '040000'}), "'../config' is not a name"),
        }
        for case, (commit, message) in cases.items():
            with self.subTest(case), self.assertRaisesRegex(ValueError, message):
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
        other = lines.index('Other entries:')
        self.assertLess(running, other)
        self.assertRegex(lines[running + 1], r'^  \+ overrides\.fish shell/notes\.fish +runs in every shell$')
        self.assertEqual(lines[other + 1].rstrip(), '  + assets ~/.local/share/example-notes/style.csl')
        text = '\n'.join(lines)
        self.assertIn('list "- alice/notes" under\nprofiles: in config.yaml', text)
        self.assertIn(f'./bin/shaffy profile propose alice/notes drafts it for the author:\n  {ISSUES}', text)
        self.assertIn('./bin/shaffy profile propose alice/notes --to shaffy', text)
        self.assertIn('With alice/notes listed in config.yaml, the settings load.', text)

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
        vendor.remove(self.supplement, ['alice/tools'])
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


class RemoveTests(CommandCase):
    def test_remove_refuses_while_listed_and_names_what_stays(self):
        self.repository.commit(
            'Add a package',
            {'notes/profile.yaml': NOTES['notes/profile.yaml'] + 'packages:\n  extra: [example-notes]\n'},
        )
        self.add('notes')
        self.config('profiles: [alice/notes]\n')
        with self.assertRaisesRegex(vendor.ProfileError, 'config.yaml still lists alice/notes'):
            vendor.remove(self.supplement, ['alice/notes'])
        self.assertTrue((self.supplement / 'profiles/alice/notes').exists())
        self.config('')
        lines = vendor.remove(self.supplement, ['alice/notes'])
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
        self.assertEqual(
            vendor.remove(self.supplement, ['alice/notes']), ['alice/notes: not vendored here; nothing to remove.']
        )


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

    def test_inside_remove_the_rerun_deletes_the_folder(self):
        self.add('notes', 'keys')
        real = vendor.shutil.rmtree

        def rmtree(path, *arguments, **keywords):
            if Path(path).name == 'notes':
                raise RuntimeError('crash')
            real(path, *arguments, **keywords)

        self.interrupted((vendor.shutil, 'rmtree'), rmtree, lambda: vendor.remove(self.supplement, ['alice/notes']))
        self.assertNotIn('alice/notes', self.lock())
        self.assertTrue((self.supplement / 'profiles/alice/notes').exists())
        vendor.remove(self.supplement, ['alice/notes'])
        self.assertFalse((self.supplement / 'profiles/alice/notes').exists())
        self.assertEqual(list(self.lock()), ['alice/keys'])

    def test_a_second_command_while_one_runs_is_refused(self):
        folder = self.supplement / profiles.FOLDER
        folder.mkdir()
        handle = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, handle)
        fcntl.flock(handle, fcntl.LOCK_EX)
        with self.assertRaisesRegex(vendor.ProfileError, 'another profile command is changing'):
            self.add('notes')
        self.assertEqual(list(folder.iterdir()), [])


class BoundaryTests(CommandCase):
    def test_commands_write_nothing_outside_profiles(self):
        home = self.directory / 'home'
        home.mkdir()
        before = tree_of(self.supplement, skip={profiles.FOLDER})
        with mock.patch.dict(os.environ, {'HOME': str(home)}):
            self.add('notes', 'keys')
            self.repository.commit('Change the style', {'notes/style.csl': '<style version="2"/>\n'})
            self.update()
            vendor.remove(self.supplement, ['alice/keys'])
        self.assertEqual(tree_of(self.supplement, skip={profiles.FOLDER}), before)  # config.yaml and .git included
        self.assertEqual(list(home.iterdir()), [])
        status = git('-C', self.supplement, 'status', '--porcelain', '--untracked-files=all')
        self.assertTrue(all(line[3:].startswith('profiles/') for line in status.splitlines()), status)

    def test_an_id_or_a_link_that_leads_out_of_profiles_is_refused(self):
        outside = self.directory / 'outside'
        (outside / 'notes').mkdir(parents=True)
        for profile_id in ('../outside', 'alice/../../outside', '/tmp/x'):
            with self.subTest(profile_id), self.assertRaisesRegex(vendor.ProfileError, 'is not a profile ID'):
                vendor.remove(self.supplement, [profile_id])
        self.add('notes')
        os.symlink(outside, self.supplement / 'profiles/bob')
        with self.assertRaisesRegex(vendor.ProfileError, 'profiles/bob is a link'):
            vendor.remove(self.supplement, ['bob/notes'])
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
        vendor.remove(self.supplement, ['alice/keys'])
        once = tree_of(self.supplement)
        vendor.remove(self.supplement, ['alice/keys'])
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


if __name__ == '__main__':
    unittest.main()
