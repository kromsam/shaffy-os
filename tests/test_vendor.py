"""The profile commands and their reads of a profile's repository, against local Git repositories.

Every repository is built in a temporary folder and served as a bare copy over file://, so no
test reaches the network, and every git reads no configuration of this machine's.
"""

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
from shaffy import pins

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


if __name__ == '__main__':
    unittest.main()
