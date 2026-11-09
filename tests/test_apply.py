"""The home operation protocol, its crash recovery and convergence."""

import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shaffy.apply import FakeSnapper
from test_render import REASON

FIXTURE = ROOT / 'tests/fixtures/supplements/comprehensive'


@unittest.skipIf(REASON, f'render sandbox unavailable: {REASON}')
class HomeApplyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.home = root / 'home'
        (self.home / '.config/tmux').mkdir(parents=True)
        (self.home / '.config/tmux/tmux.conf').write_text("# the user's own tmux\n")
        self.supplement = root / 'supplement'
        shutil.copytree(FIXTURE, self.supplement)
        self.env = {
            'HOME': str(self.home),
            'PATH': '/usr/bin:/bin',
            'LANG': 'C.UTF-8',
            'SHAFFY_TEST_PREREQUISITES': 'fake',
        }
        if os.environ.get('SHAFFY_RENDER_DIRECT'):
            self.env['SHAFFY_RENDER_DIRECT'] = '1'
        self.state = self.home / '.local/state/shaffy'

    def shaffy(self, *arguments, fault=None, checkout=ROOT):
        env = dict(self.env, **({'SHAFFY_TEST_FAULT': fault} if fault else {}))
        result = subprocess.run(
            [str(checkout / 'bin/shaffy'), *arguments],
            capture_output=True,
            text=True,
            env=env,
            timeout=300,
            check=False,
        )
        self.assertNotIn('Traceback', result.stderr)
        return result

    def copy_checkout(self):
        copy = Path(self.temporary.name) / 'other-checkout'
        shutil.copytree(
            ROOT,
            copy,
            symlinks=True,
            ignore=shutil.ignore_patterns('.git', 'artifacts', '__pycache__', 'supplement', 'logs', 'secrets'),
        )
        return copy

    def snapshots(self):
        lines = (self.state / 'fake-snapshots.jsonl').read_text().splitlines()
        return [(s['type'], s['pre']) for s in map(json.loads, lines)]

    def runs(self):
        return sorted(p.name for p in (self.state / 'runs').iterdir()) if (self.state / 'runs').exists() else []

    def record(self):
        return json.loads((self.state / 'binding.json').read_text())

    def ledger(self):
        return json.loads((self.state / 'ledger.json').read_text())['integrations']

    def set_integration(self, name, state):
        config = self.supplement / 'config.yaml'
        text = re.sub(rf'^  {name}: \w+\n', '', config.read_text(), flags=re.M)
        config.write_text(text + (f'  {name}: {state}\n' if state else ''))

    def events(self, op=None):
        lines = (self.state / 'journal.jsonl').read_text().splitlines()
        return [e['event'] for e in map(json.loads, lines) if op is None or e['op'] == op]

    def codex(self):
        return (self.home / '.codex/config.toml').read_text()

    def attach(self, checkout=ROOT):
        result = self.shaffy('supplement', 'attach', '--supplement', str(self.supplement), checkout=checkout)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_apply_needs_a_binding(self):
        result = self.shaffy('apply', '--home')
        self.assertEqual(result.returncode, 1)
        self.assertIn('supplement attach', result.stderr)
        self.assertFalse((self.home / '.codex').exists())

    def test_defaults_follow_platform_directory_changes_without_owning_the_file(self):
        dirs = self.home / '.config/user-dirs.dirs'
        dirs.write_text('# Installation\nXDG_PICTURES_DIR="$HOME/Images"\n')
        attached = self.shaffy('supplement', 'attach', '--none')
        self.assertEqual(attached.returncode, 0, attached.stderr)
        for name in ('Images', '画像'):
            text = f'# Installation\nXDG_PICTURES_DIR="$HOME/{name}"\n'
            dirs.write_text(text)  # simulate the platform updating its own XDG setting
            applied = self.shaffy('apply', '--home')
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertEqual(dirs.read_text(), text)
            self.assertEqual(dirs.stat().st_mode & 0o777, 0o644)
            shell = tomllib.loads((self.home / '.config/noctalia/90-shell.toml').read_text())
            self.assertEqual(shell['shell']['screenshot']['directory'], f'{self.home}/{name}/Screenshots')
        repeated = self.shaffy('apply', '--home')
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertIn('(0 target(s) changed)', repeated.stdout)

    def test_removing_a_folder_override_preserves_the_xdg_entry(self):
        self.attach()
        first = self.shaffy('apply', '--home')
        self.assertEqual(first.returncode, 0, first.stderr)
        dirs = self.home / '.config/user-dirs.dirs'
        original = dirs.read_text()
        config = self.supplement / 'config.yaml'
        config.write_text(config.read_text().replace('folders:\n  pictures: "Bilder"\n  videos: "Filme"\n', ''))
        second = self.shaffy('apply', '--home')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(dirs.read_text(), original)
        self.assertIn('(0 target(s) changed)', second.stdout)

    def test_apply_snapshots_copies_applies_and_converges(self):
        self.attach()
        first = self.shaffy('apply', '--home')
        self.assertEqual(first.returncode, 0, first.stderr)
        record = self.record()
        op = record['last_completed_op']
        self.assertIsNone(record['pending'])
        self.assertEqual(self.events(op), ['begin', 'phase-start', 'ledger-intent', 'ledger-done', 'phase-done'])
        self.assertIn('[mcp_servers.notes]', self.codex())
        before = self.state / 'runs' / op / 'before/.config/tmux/tmux.conf'
        self.assertEqual(before.read_text(), "# the user's own tmux\n")
        self.assertEqual(self.snapshots(), [('pre', None), ('post', 1)])
        # New user unit files need the running user manager reloaded; the test adapter only says so.
        self.assertIn('systemctl --user daemon-reload', first.stdout)
        second = self.shaffy('apply', '--home')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('(0 target(s) changed)', second.stdout)
        self.assertNotIn('daemon-reload', second.stdout)
        self.assertEqual(self.events(self.record()['last_completed_op']), ['begin', 'phase-start', 'phase-done'])

    def test_disabling_an_mcp_server_removes_it_from_every_client_and_converges(self):
        self.attach()
        first = self.shaffy('apply', '--home')
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn('zotero', tomllib.loads(self.codex())['mcp_servers'])
        desktop = self.home / '.config/Claude/claude_desktop_config.json'
        self.assertIn('zotero', json.loads(desktop.read_text())['mcpServers'])
        config = self.supplement / 'config.yaml'
        config.write_text(config.read_text().replace('    zotero:\n', '    zotero:\n      enabled: false\n'))
        applied = self.shaffy('apply', '--home')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        codex = tomllib.loads(self.codex())['mcp_servers']
        claude = json.loads((self.home / '.claude.json').read_text())['mcpServers']
        for servers in (codex, claude, json.loads(desktop.read_text())['mcpServers']):
            self.assertNotIn('zotero', servers)
            self.assertIn('notes', servers)
        second = self.shaffy('apply', '--home')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('(0 target(s) changed)', second.stdout)

    def test_changed_unit_files_alone_still_ask_for_a_reload(self):
        from shaffy.apply import HomeApply

        messages = []
        with mock.patch.dict(os.environ, self.env):
            home_apply = HomeApply(ROOT, None, messages.append)
            home_apply._start_units([])
            self.assertEqual(messages, [])
            home_apply.units_changed = True
            home_apply._start_units([])
        self.assertIn('user unit files changed; run systemctl --user daemon-reload', messages[0])

    def test_assets_come_from_the_capture_and_follow_their_source(self):
        self.attach()
        self.assertEqual(self.shaffy('apply', '--home').returncode, 0)
        font = self.home / '.local/share/fonts/Example-Regular.otf'
        self.assertEqual(font.read_text(), 'OTTO synthetic font bytes\n')
        op = self.record()['last_completed_op']
        self.assertTrue((self.state / 'runs' / op / 'capture/supplement/assets/fonts/Example-Regular.otf').is_file())
        (self.supplement / 'assets/fonts/Example-Regular.otf').write_text('OTTO version 2\n')
        self.assertIn('Example-Regular.otf differ', self.shaffy('diff', str(font)).stdout)  # shown as binary
        result = self.shaffy('apply', '--home')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(font.read_text(), 'OTTO version 2\n')
        copy = (
            self.state / 'runs' / self.record()['last_completed_op'] / 'before/.local/share/fonts/Example-Regular.otf'
        )
        self.assertEqual(copy.read_text(), 'OTTO synthetic font bytes\n')

    def test_an_asset_never_replaces_a_folder_or_goes_through_a_link(self):
        folder = self.home / '.local/share/fonts/Example-Regular.otf'
        folder.mkdir(parents=True)
        (folder / 'keep.txt').write_text('mine')
        self.attach()
        refused = self.shaffy('apply', '--home')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('is a real folder, which the copy would replace', refused.stderr)
        self.assertEqual((folder / 'keep.txt').read_text(), 'mine')
        shutil.rmtree(folder)
        (self.home / 'Plaatjes').mkdir()
        (self.home / 'Wallpapers').symlink_to('Plaatjes')
        linked = self.shaffy('apply', '--home')
        self.assertEqual(linked.returncode, 1)
        self.assertIn('is a link, which the copy would replace with a folder', linked.stderr)
        self.assertTrue((self.home / 'Wallpapers').is_symlink())

    def test_live_edits_appear_in_the_native_diff_and_status(self):
        self.attach()
        self.assertEqual(self.shaffy('apply', '--home').returncode, 0)
        config = self.supplement / 'config.yaml'
        config.write_text(config.read_text().replace('ZOTERO_LOCAL_WRITE: "true"', 'ZOTERO_LOCAL_WRITE: "false"'))
        diff = self.shaffy('diff', str(self.home / '.codex/config.toml'))
        self.assertEqual(diff.returncode, 0, diff.stderr)
        self.assertIn('-ZOTERO_LOCAL_WRITE = "true"', diff.stdout)
        self.assertIn('+ZOTERO_LOCAL_WRITE = "false"', diff.stdout)
        self.assertIn('.codex/config.toml', self.shaffy('status').stdout)
        self.assertIn('ZOTERO_LOCAL_WRITE = "true"', self.codex())  # nothing was applied

    def test_a_removed_override_clears_its_file(self):
        self.attach()
        self.assertEqual(self.shaffy('apply', '--home').returncode, 0)
        fish = self.home / '.config/fish/supplement.fish'
        self.assertIn('synthetic-supplement-greeting', fish.read_text())
        config = self.supplement / 'config.yaml'
        config.write_text(config.read_text().replace('  fish: overrides/fish.fish\n', ''))
        result = self.shaffy('apply', '--home')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(fish.exists())
        self.assertEqual(fish.read_text(), '')

    def test_a_directory_replaced_by_a_file_is_copied_first(self):
        notes = self.home / '.config/fish/supplement.fish/notes.txt'
        notes.parent.mkdir(parents=True)
        notes.write_text('kept\n')
        self.attach()
        result = self.shaffy('apply', '--home')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / '.config/fish/supplement.fish').is_file())
        op = self.record()['last_completed_op']
        copy = self.state / 'runs' / op / 'before/.config/fish/supplement.fish/notes.txt'
        self.assertEqual(copy.read_text(), 'kept\n')
        # .local only changes permissions (private_dot_local), so its contents are not copied.
        self.assertFalse((self.state / 'runs' / op / 'before/.local/state').exists())

    def test_another_chezmoi_holding_its_state_writes_nothing_and_clears_the_operation(self):
        import fcntl

        self.attach()
        self.assertEqual(self.shaffy('apply', '--home').returncode, 0)
        state = self.home / '.local/state/shaffy/chezmoistate.boltdb'
        config = self.supplement / 'config.yaml'
        config.write_text(config.read_text().replace('ZOTERO_LOCAL_WRITE: "true"', 'ZOTERO_LOCAL_WRITE: "false"'))
        with open(state, 'rb') as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)  # what a concurrent read-only chezmoi holds
            refused = self.shaffy('apply', '--home')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('another chezmoi command is running; nothing was changed', refused.stderr)
        self.assertIsNone(self.record()['pending'])
        self.assertEqual(self.events()[-1], 'phase-not-started')
        self.assertIn('ZOTERO_LOCAL_WRITE = "true"', self.codex())

    def test_the_desktop_key_is_built_outside_the_checkout_and_installed(self):
        installed = self.home / '.local/bin/shaffy-desktop-key'
        installed.parent.mkdir(parents=True)
        installed.write_text('an older build\n')
        self.attach()
        result = self.shaffy('apply', '--home')
        self.assertEqual(result.returncode, 0, result.stderr)
        built = self.home / '.cache/shaffy/build/desktop-key/shaffy-desktop-key'
        self.assertEqual(installed.read_bytes(), built.read_bytes())
        self.assertEqual(installed.read_bytes()[:4], b'\x7fELF')
        self.assertEqual(installed.stat().st_mode & 0o777, 0o755)
        op = self.record()['last_completed_op']
        before = self.state / 'runs' / op / 'before/.local/bin/shaffy-desktop-key'
        self.assertEqual(before.read_text(), 'an older build\n')
        second = self.shaffy('apply', '--home')
        self.assertIn('(0 target(s) changed)', second.stdout)

    def test_an_applied_home_that_does_not_validate_stays_unfinished(self):
        # themes/noctalia.toml is Noctalia's to write; chezmoi only creates it when missing.
        theme = self.home / '.config/alacritty/themes/noctalia.toml'
        theme.parent.mkdir(parents=True)
        theme.write_text('[colors\n')
        self.attach()
        failed = self.shaffy('apply', '--home')
        self.assertEqual(failed.returncode, 1)
        self.assertIn('does not validate', failed.stderr)
        self.assertIn('themes/noctalia.toml', failed.stderr)
        op = self.record()['pending']['op']
        self.assertEqual(self.events(op)[-1], 'phase-failed')
        theme.write_text('[colors.primary]\nbackground = "#faf4ed"\n')
        resumed = self.shaffy('apply', '--resume')
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(self.events(op)[-1], 'phase-done')

    def test_a_changed_supplement_id_needs_a_rebind(self):
        self.attach()
        config = self.supplement / 'config.yaml'
        config.write_text(config.read_text().replace('supplement_id: synthetic-desktop', 'supplement_id: renamed'))
        result = self.shaffy('apply', '--home')
        self.assertEqual(result.returncode, 1)
        self.assertIn('--new-id', result.stderr)
        self.assertEqual(self.runs(), [])
        self.assertFalse((self.home / '.codex').exists())

    def test_a_different_supplement_is_refused(self):
        self.attach()
        result = self.shaffy('apply', '--home', '--supplement', str(ROOT / 'supplement.example'))
        self.assertEqual(result.returncode, 1)
        self.assertIn('rebind', result.stderr)

    def test_kill_before_any_write_is_cleared_by_resume(self):
        self.attach()
        killed = self.shaffy('apply', '--home', fault='after-pending')
        self.assertEqual(killed.returncode, -signal.SIGKILL)
        self.assertIsNotNone(self.record()['pending'])
        self.assertIn('Nothing was written yet', self.shaffy('status', '--no-drift').stdout)
        refused = self.shaffy('apply', '--home')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('apply --resume', refused.stderr)
        resumed = self.shaffy('apply', '--resume')
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertIn('had written nothing', resumed.stdout)
        self.assertIsNone(self.record()['pending'])
        self.assertFalse((self.home / '.codex').exists())

    def test_kill_mid_phase_resumes_from_the_captured_settings(self):
        self.attach()
        killed = self.shaffy('apply', '--home', fault='after-phase-start')
        self.assertEqual(killed.returncode, -signal.SIGKILL)
        op = self.record()['pending']['op']
        self.assertIn('Snapper pre snapshot 1', self.shaffy('status', '--no-drift').stdout)
        # An edit after the capture must not reach this operation.
        config = self.supplement / 'config.yaml'
        config.write_text(config.read_text().replace('ZOTERO_LOCAL_WRITE: "true"', 'ZOTERO_LOCAL_WRITE: "false"'))
        resumed = self.shaffy('apply', '--resume')
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertIsNone(self.record()['pending'])
        self.assertEqual(self.record()['last_completed_op'], op)
        self.assertIn('ZOTERO_LOCAL_WRITE = "true"', self.codex())
        self.assertEqual(
            self.events(op), ['begin', 'phase-start', 'resume-start', 'ledger-intent', 'ledger-done', 'phase-done']
        )
        # The resume took its own pre snapshot and copies; the post snapshot pairs with it.
        self.assertEqual(self.snapshots(), [('pre', None), ('pre', None), ('post', 2)])
        self.assertTrue((self.state / 'runs' / op / 'before-resume-1').is_dir())

    def test_kill_after_chezmoi_resumes_idempotently(self):
        self.attach()
        killed = self.shaffy('apply', '--home', fault='after-chezmoi')
        self.assertEqual(killed.returncode, -signal.SIGKILL)
        resumed = self.shaffy('apply', '--resume')
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertIsNone(self.record()['pending'])
        self.assertIn('[mcp_servers.notes]', self.codex())

    def test_preflight_failure_changes_nothing(self):
        self.attach()
        (self.supplement / 'config.yaml').write_text('schema_version: 1\nsupplement_id: t\ncolour: x\n')
        result = self.shaffy('apply', '--home')
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown key 'colour'", result.stderr)
        self.assertIsNone(self.record()['pending'])
        self.assertFalse((self.home / '.codex').exists())
        self.assertEqual(self.runs(), [])

    def test_targets_chezmoi_removes_are_copied_first(self):
        # Real retirements expire (lib/shaffy/sunset.py), so retire a synthetic file in a copy.
        checkout = self.copy_checkout()
        with open(checkout / 'home/.chezmoiremove', 'a') as removals:
            removals.write('.local/bin/example-retired\n')
        retired = self.home / '.local/bin/example-retired'
        retired.parent.mkdir(parents=True, exist_ok=True)
        retired.write_text('probe\n')
        self.attach(checkout=checkout)
        result = self.shaffy('apply', '--home', checkout=checkout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(retired.exists())
        op = self.record()['last_completed_op']
        copy = self.state / 'runs' / op / 'before' / '.local/bin/example-retired'
        self.assertEqual(copy.read_text(), 'probe\n')

    def test_the_renamed_niri_and_noctalia_files_replace_the_old_names(self):
        # Remove after 2027-02-06, with the old names in home/.chezmoiremove.
        old = [self.home / '.config/niri/omarchy-overrides.kdl', self.home / '.config/noctalia/90-omarchy.toml']
        for path in old:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('// the old name\n')
        self.attach()
        result = self.shaffy('apply', '--home')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([path.exists() for path in old], [False, False])
        self.assertTrue((self.home / '.config/niri/runtime-overrides.kdl').exists())
        self.assertTrue((self.home / '.config/noctalia/90-shell.toml').exists())

    def test_old_runs_are_pruned_to_the_newest_ten_completed(self):
        self.attach()
        with open(self.state / 'journal.jsonl', 'a') as journal:
            for number in range(12):
                old = f'20000101T0000{number:02d}Z-old'
                (self.state / f'runs/{old}/before').mkdir(parents=True)
                journal.write(json.dumps({'at': 'x', 'op': old, 'event': 'phase-done'}) + '\n')
        abandoned = '19990101T000000Z-abandoned'  # older than all, but its copies await review
        (self.state / f'runs/{abandoned}/before').mkdir(parents=True)
        self.assertEqual(self.shaffy('apply', '--home').returncode, 0)
        op = self.record()['last_completed_op']
        self.assertEqual(len(self.runs()), 11)
        self.assertIn(op, self.runs())
        self.assertIn(abandoned, self.runs())
        self.assertNotIn('20000101T000000Z-old', self.runs())

    def test_a_moved_supplement_names_rebind(self):
        self.attach()
        self.supplement.rename(self.supplement.with_name('moved'))
        result = self.shaffy('apply', '--home')
        self.assertEqual(result.returncode, 1)
        self.assertIn('supplement rebind --path', result.stderr)
        self.assertEqual(self.runs(), [])

    def test_another_checkout_applies_only_when_named(self):
        self.attach()
        other = self.copy_checkout()
        refused = self.shaffy('apply', '--home', checkout=other)
        self.assertEqual(refused.returncode, 1)
        self.assertIn(f'--checkout {other}', refused.stderr)
        self.assertFalse((self.home / '.codex').exists())
        named = self.shaffy('apply', '--home', '--checkout', str(other), checkout=other)
        self.assertEqual(named.returncode, 0, named.stderr)
        op = self.record()['last_completed_op']
        self.assertEqual(
            self.events(op), ['checkout-override', 'begin', 'phase-start', 'ledger-intent', 'ledger-done', 'phase-done']
        )

    def test_resume_only_in_the_checkout_the_operation_began_in(self):
        self.attach()
        self.assertEqual(self.shaffy('apply', '--home', fault='after-phase-start').returncode, -signal.SIGKILL)
        other = self.copy_checkout()
        refused = self.shaffy('apply', '--resume', checkout=other)
        self.assertEqual(refused.returncode, 1)
        self.assertIn(f'began in {ROOT}', refused.stderr)
        self.assertIsNotNone(self.record()['pending'])

    def test_resume_runs_the_preflight_and_writes_nothing_when_it_fails(self):
        self.attach()
        self.assertEqual(self.shaffy('apply', '--home', fault='after-phase-start').returncode, -signal.SIGKILL)
        op = self.record()['pending']['op']
        folder = self.home / '.local/share/fonts/Example-Regular.otf'
        folder.mkdir(parents=True)
        (folder / 'keep.txt').write_text('mine')
        refused = self.shaffy('apply', '--resume')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('cannot resume', refused.stderr)
        self.assertIn('is a real folder, which the copy would replace', refused.stderr)
        self.assertEqual(self.record()['pending']['op'], op)
        self.assertEqual(self.events(op)[-1], 'resume-refused')
        self.assertEqual((folder / 'keep.txt').read_text(), 'mine')
        self.assertFalse((self.home / '.codex').exists())

    def test_changed_defaults_stop_a_resume(self):
        other = self.copy_checkout()
        self.attach(checkout=other)
        self.assertEqual(
            self.shaffy('apply', '--home', fault='after-phase-start', checkout=other).returncode, -signal.SIGKILL
        )
        defaults = other / 'defaults/config.yaml'
        original = defaults.read_text()
        changed = original.replace('browser: "zen.desktop"', 'browser: "example-browser.desktop"')
        self.assertNotEqual(changed, original)
        defaults.write_text(changed)
        refused = self.shaffy('apply', '--resume', checkout=other)
        self.assertEqual(refused.returncode, 1)
        self.assertIn('effective settings differ', refused.stderr)
        self.assertIsNotNone(self.record()['pending'])

    def test_a_template_change_before_resume_is_journaled(self):
        other = self.copy_checkout()
        self.attach(checkout=other)
        self.assertEqual(
            self.shaffy('apply', '--home', fault='after-phase-start', checkout=other).returncode, -signal.SIGKILL
        )
        op = self.record()['pending']['op']
        with (other / 'home/dot_config/tmux/tmux.conf').open('a') as stream:
            stream.write('# changed before the resume\n')
        resumed = self.shaffy('apply', '--resume', checkout=other)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertIn('source-changed', self.events(op))
        self.assertIn('# changed before the resume', (self.home / '.config/tmux/tmux.conf').read_text())

    def test_abandon_clears_the_operation_and_names_what_to_review(self):
        self.attach()
        self.assertEqual(self.shaffy('apply', '--home', fault='after-phase-start').returncode, -signal.SIGKILL)
        op = self.record()['pending']['op']
        shutil.rmtree(self.state / 'runs' / op / 'capture')
        refused = self.shaffy('apply', '--resume')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('apply --abandon', refused.stderr)
        self.assertIn('--abandon', self.shaffy('status', '--no-drift').stdout)
        abandoned = self.shaffy('apply', '--abandon')
        self.assertEqual(abandoned.returncode, 0, abandoned.stderr)
        self.assertIn('pre snapshot(s) 1', abandoned.stdout)
        self.assertIn(str(self.state / 'runs' / op / 'before'), abandoned.stdout)
        self.assertIsNone(self.record()['pending'])
        self.assertEqual(self.events(op)[-1], 'abandoned')
        self.assertTrue((self.state / 'runs' / op / 'before').is_dir())
        self.assertEqual(self.shaffy('apply', '--home').returncode, 0)

    # -- integrations and the ownership ledger ----------------
    SYNCTHING = '.config/systemd/user/default.target.wants/syncthing.service'

    def test_a_fresh_home_manages_the_requested_integrations(self):
        self.attach()
        result = self.shaffy('apply', '--home')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Integration syncthing is now managed.', result.stdout)
        self.assertEqual(os.readlink(self.home / self.SYNCTHING), '/usr/lib/systemd/user/syncthing.service')
        ledger = self.ledger()
        self.assertEqual(sorted(ledger), ['nextcloud', 'reaper', 'ssh', 'syncthing', 'zotero'])  # not zettlr
        entry = ledger['syncthing']
        self.assertEqual((entry['state'], entry['adopted'], entry['transition']), ('managed', False, None))
        self.assertEqual(entry['created_by'], self.record()['last_completed_op'])
        self.assertEqual(oct((self.state / 'ledger.json').stat().st_mode & 0o777), '0o600')
        self.assertFalse((self.home / '.config/Zettlr').exists())

    def test_activation_already_present_must_be_adopted_first(self):
        link = self.home / self.SYNCTHING
        link.parent.mkdir(parents=True)
        link.symlink_to('/usr/lib/systemd/user/syncthing.service')
        self.attach()
        refused = self.shaffy('apply', '--home')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('integration syncthing is already active', refused.stderr)
        self.assertIn('./bin/shaffy integration syncthing adopt', refused.stderr)
        self.assertFalse((self.state / 'ledger.json').exists())
        self.assertFalse((self.home / '.codex').exists())
        missing = self.shaffy('integration', 'ssh', 'adopt')
        self.assertEqual(missing.returncode, 1)
        self.assertIn('nothing of integration ssh is active here', missing.stderr)
        adopted = self.shaffy('integration', 'syncthing', 'adopt')
        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        self.assertEqual(
            (self.ledger()['syncthing']['state'], self.ledger()['syncthing']['adopted']), ('managed', True)
        )
        self.assertIn('ledger-adopt', self.events())
        self.assertIn('Integration syncthing: managed (adopted)', self.shaffy('status', '--no-drift').stdout)
        self.assertEqual(self.shaffy('integration', 'syncthing', 'adopt').returncode, 1)  # already managed
        applied = self.shaffy('apply', '--home')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertNotIn('syncthing is now', applied.stdout)
        self.assertTrue(self.ledger()['syncthing']['adopted'])

    def test_an_existing_zotero_profile_is_moved_then_adopted(self):
        root = self.home / '.zotero/zotero'
        (root / 'abcd1234.default').mkdir(parents=True)
        (root / 'abcd1234.default/prefs.js').write_text('user_pref("a", 1);\n')
        (root / 'profiles.ini').write_text(
            '[Profile0]\nName=default\nIsRelative=1\nPath=abcd1234.default\n'
            'Default=1\n\n[General]\nStartWithLastProfile=1\nVersion=2\n\n'
        )
        self.attach()
        refused = self.shaffy('apply', '--home')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('integration zotero is already active', refused.stderr)
        self.assertIn('move-zotero-profile.py --apply, then ./bin/shaffy integration zotero adopt', refused.stderr)
        blocked = self.shaffy('integration', 'zotero', 'adopt')
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("does not hold 'Path=shaffy.default' yet", blocked.stderr)
        self.assertIn('scripts/move-zotero-profile.py --apply', blocked.stderr)
        moved = subprocess.run(
            [sys.executable, str(ROOT / 'scripts/move-zotero-profile.py'), '--apply'],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )
        self.assertEqual(moved.returncode, 0, moved.stderr)
        self.assertEqual(self.shaffy('integration', 'zotero', 'adopt').returncode, 0)
        applied = self.shaffy('apply', '--home')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual((root / 'shaffy.default/prefs.js').read_text(), 'user_pref("a", 1);\n')
        self.assertIn(f'"{self.home}/.local/share/zotero-data"', (root / 'shaffy.default/user.js').read_text())
        op = self.record()['last_completed_op']
        self.assertFalse((self.state / 'runs' / op / 'before/.zotero/zotero/profiles.ini').exists())  # unchanged

    def test_backup_timers_wait_for_a_verified_backup_of_the_configured_repository(self):
        self.attach()
        self.set_integration('backup', 'managed')
        # A fresh home installs the client and defers the timers, instead of refusing the apply.
        first = self.shaffy('apply', '--home')
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn('Integration backup waits:', first.stdout)
        self.assertIn('run restic-home backup once', first.stdout)
        self.assertTrue((self.home / '.local/bin/restic-home').is_file())
        wants = self.home / '.config/systemd/user/timers.target.wants'
        self.assertEqual(list(wants.iterdir()) if wants.exists() else [], [])
        self.assertNotIn('backup', self.ledger())
        self.assertIn('Integration backup: waits', self.shaffy('status', '--no-drift').stdout)
        receipt = self.home / '.local/state/shaffy/home-backup/last-success.json'
        # The apply creates the folder the timer-run services may write to, before any receipt.
        self.assertEqual(receipt.parent.stat().st_mode & 0o777, 0o700)
        receipt.write_text(json.dumps({'status': 'verified', 'repository_id': 'f' * 64}, indent=2) + '\n')
        other = self.shaffy('apply', '--home')  # a receipt of another repository does not count
        self.assertEqual(other.returncode, 0, other.stderr)
        self.assertIn('Integration backup waits:', other.stdout)
        receipt.write_text(json.dumps({'status': 'verified', 'repository_id': '0123456789abcdef' * 4}) + '\n')
        applied = self.shaffy('apply', '--home')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(
            sorted(p.name for p in wants.iterdir()),
            ['restic-home-backup.timer', 'restic-home-check.timer', 'restic-home-maintenance.timer'],
        )
        self.assertEqual(
            os.readlink(wants / 'restic-home-check.timer'),
            str(self.home / '.config/systemd/user/restic-home-check.timer'),
        )
        # The test adapter never starts units in a real user manager; it says how instead.
        self.assertIn(
            'restic-home-backup.timer, restic-home-maintenance.timer, restic-home-check.timer start at the next login',
            applied.stdout,
        )
        self.set_integration('backup', 'disabled')
        self.assertEqual(self.shaffy('apply', '--home').returncode, 0)
        self.assertEqual(list(wants.iterdir()), [])
        self.assertTrue((self.home / '.config/restic-home/backup.json').is_file())  # configuration stays

    def test_a_lock_timeout_withdraws_the_announced_transitions(self):
        import fcntl

        self.attach()
        self.assertEqual(self.shaffy('apply', '--home').returncode, 0)
        before = self.ledger()
        self.set_integration('syncthing', 'disabled')
        state = self.home / '.local/state/shaffy/chezmoistate.boltdb'
        with open(state, 'rb') as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)  # chezmoi status still reads; apply cannot write
            refused = self.shaffy('apply', '--home')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('nothing was changed', refused.stderr)
        self.assertEqual(self.events()[-3:], ['ledger-intent', 'phase-not-started', 'ledger-intent-withdrawn'])
        self.assertEqual(self.ledger(), before)
        self.assertTrue(os.path.lexists(self.home / self.SYNCTHING))
        again = self.shaffy('apply', '--home')
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.ledger()['syncthing']['state'], 'disabled')

    def test_disabled_reverses_the_recorded_activation_and_keeps_the_rest(self):
        self.attach()
        self.assertEqual(self.shaffy('apply', '--home').returncode, 0)
        ssh = self.home / '.ssh/config'
        self.assertTrue(ssh.is_file())
        self.set_integration('syncthing', 'disabled')
        self.set_integration('ssh', 'disabled')
        result = self.shaffy('apply', '--home')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Integration syncthing is now disabled. A service it enabled keeps running', result.stdout)
        self.assertFalse(os.path.lexists(self.home / self.SYNCTHING))
        self.assertFalse(os.path.lexists(self.home / '.config/systemd/user/sockets.target.wants/ssh-agent.socket'))
        self.assertTrue(ssh.is_file())  # configuration is ignored from now on, never removed
        # Nothing points SSH at the agent socket any more.
        self.assertFalse((self.home / '.config/environment.d/30-ssh-agent.conf').exists())
        op = self.record()['last_completed_op']
        copy = self.state / 'runs' / op / 'before' / self.SYNCTHING
        self.assertEqual(os.readlink(copy), '/usr/lib/systemd/user/syncthing.service')
        self.assertEqual(self.ledger()['syncthing']['state'], 'disabled')
        again = self.shaffy('apply', '--home')
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn('(0 target(s) changed)', again.stdout)
        self.set_integration('syncthing', 'managed')
        back = self.shaffy('apply', '--home')
        self.assertEqual(back.returncode, 0, back.stderr)
        self.assertTrue(os.path.lexists(self.home / self.SYNCTHING))
        self.assertEqual(self.ledger()['syncthing']['state'], 'managed')

    def test_a_managed_integration_left_out_of_the_settings_is_refused(self):
        self.attach()
        applied = self.shaffy('apply', '--home')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.set_integration('syncthing', None)
        refused = self.shaffy('apply', '--home')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('set integrations.syncthing to disabled or released', refused.stderr)
        self.assertIsNone(self.record()['pending'])
        self.assertIn(
            'Integration awaiting a decision: integration syncthing is managed',
            self.shaffy('status', '--no-drift').stdout,
        )
        self.set_integration('syncthing', 'released')
        released = self.shaffy('apply', '--home')
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertIn('Integration syncthing is now released.', released.stdout)
        self.assertNotIn('syncthing', self.ledger())
        self.assertTrue(os.path.lexists(self.home / self.SYNCTHING))  # left exactly as it was
        self.set_integration('syncthing', None)
        self.assertEqual(self.shaffy('apply', '--home').returncode, 0)

    def test_release_stops_tracking_and_changes_nothing(self):
        self.attach()
        self.assertEqual(self.shaffy('apply', '--home').returncode, 0)
        self.assertEqual(self.shaffy('integration', 'zettlr', 'release').returncode, 1)
        released = self.shaffy('integration', 'syncthing', 'release')
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertNotIn('syncthing', self.ledger())
        self.assertTrue(os.path.lexists(self.home / self.SYNCTHING))
        # The settings still ask for it and the link is there: adopting it is the way back.
        refused = self.shaffy('apply', '--home')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('integration syncthing adopt', refused.stderr)

    def test_a_change_resumed_after_chezmoi_completes(self):
        self.attach()
        killed = self.shaffy('apply', '--home', fault='after-chezmoi')
        self.assertEqual(killed.returncode, -signal.SIGKILL)
        op = self.record()['pending']['op']
        self.assertEqual(self.ledger()['syncthing']['transition']['op'], op)
        self.assertIn(
            f'Integration syncthing: not yet managed; changing to managed in operation {op}',
            self.shaffy('status', '--no-drift').stdout,
        )
        resumed = self.shaffy('apply', '--resume')
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(
            (self.ledger()['syncthing']['state'], self.ledger()['syncthing']['transition']), ('managed', None)
        )

    def test_an_abandoned_change_waits_for_adopt_or_release(self):
        self.attach()
        self.assertEqual(self.shaffy('apply', '--home', fault='after-chezmoi').returncode, -signal.SIGKILL)
        abandoned = self.shaffy('apply', '--abandon')
        self.assertEqual(abandoned.returncode, 0, abandoned.stderr)
        self.assertIn('Integration syncthing was changing; review it', abandoned.stdout)
        self.assertIn('Left by an abandoned operation', self.shaffy('status', '--no-drift').stdout)
        refused = self.shaffy('apply', '--home')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('left changing by abandoned operation', refused.stderr)
        for name in ('nextcloud', 'reaper', 'ssh', 'syncthing', 'zotero'):
            action = 'release' if name == 'reaper' else 'adopt'
            result = self.shaffy('integration', name, action)
            self.assertEqual(result.returncode, 0, (name, result.stderr))
        self.set_integration('reaper', 'released')
        applied = self.shaffy('apply', '--home')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(sorted(self.ledger()), ['nextcloud', 'ssh', 'syncthing', 'zotero'])
        self.assertTrue(self.ledger()['syncthing']['adopted'])

    def test_a_torn_journal_line_for_the_operation_blocks_resume(self):
        self.attach()
        self.assertEqual(self.shaffy('apply', '--home', fault='after-phase-start').returncode, -signal.SIGKILL)
        op = self.record()['pending']['op']
        with open(self.state / 'journal.jsonl', 'a') as stream:
            stream.write(f'{{"op": "{op}", "event": "phase-')
        refused = self.shaffy('apply', '--resume')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('unreadable lines', refused.stderr)
        self.assertIn('unreadable line', self.shaffy('status', '--no-drift').stdout)


class SigningNoteTests(unittest.TestCase):
    def test_a_missing_key_is_reported_and_a_present_one_is_not(self):
        from shaffy.identity import signing_note

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            settings = {'identity': {'signing': {'key': '~/.ssh/id_ed25519.pub'}}}
            self.assertIn('stay unsigned', signing_note(settings, home))
            (home / '.ssh').mkdir()
            (home / '.ssh/id_ed25519.pub').write_text('ssh-ed25519 AAAA test\n')
            self.assertIsNone(signing_note(settings, home))
            self.assertIsNone(signing_note({}, home))


class AdapterTests(unittest.TestCase):
    def test_the_test_adapter_is_refused_for_the_real_home(self):
        import pwd

        real = Path(pwd.getpwuid(os.getuid()).pw_dir)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(Path, 'home', return_value=real):
            self.assertTrue(FakeSnapper(directory).problems())
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(Path, 'home', return_value=Path(directory)):
            self.assertEqual(FakeSnapper(directory).problems(), [])


class NoctaliaSourceTests(unittest.TestCase):
    def test_git_sources_without_a_clone_are_named(self):
        from shaffy.apply import unfetched_plugin_sources

        listing = (
            'official git https://github.com/noctalia-dev/official-plugins\n'
            'community git https://github.com/noctalia-dev/community-plugins\n'
            'mine local /somewhere\n'
        )
        with tempfile.TemporaryDirectory() as state:
            # Noctalia makes the source folder before it clones; only repo/ means a fetched source.
            (Path(state) / 'noctalia/plugins/sources/official/repo').mkdir(parents=True)
            (Path(state) / 'noctalia/plugins/sources/community').mkdir(parents=True)
            self.assertEqual(unfetched_plugin_sources(listing, state), ['community'])
            self.assertEqual(unfetched_plugin_sources('', state), [])


if __name__ == '__main__':
    unittest.main()
