"""pstack configuration must converge without changing other agent capabilities."""

import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MERGE = runpy.run_path(str(ROOT / 'scripts/merge-ai-config.py'))
INSTALL = runpy.run_path(str(ROOT / 'scripts/install-pstack.py'))
PLUGIN = MERGE['PSTACK_PLUGIN']
RUBY_LSP = 'ruby-lsp@claude-plugins-official'
MARKETPLACE = MERGE['PSTACK_MARKETPLACE']


class PstackTests(unittest.TestCase):
    def test_claude_preserves_hooks_permissions_and_other_plugins(self):
        original = {
            'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'workmux status'}]}]},
            'permissions': {'deny': ['Bash(sudo:*)']},
            'enabledPlugins': {'other@official': True},
            'extraKnownMarketplaces': {'other': {'source': {'source': 'github', 'repo': 'org/other'}}},
        }
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                output = MERGE['merge_claude_settings'](json.dumps(original), enabled)
                actual = json.loads(output)
                for key in ('hooks', 'permissions'):
                    self.assertEqual(actual[key], original[key])
                self.assertTrue(actual['enabledPlugins']['other@official'])
                self.assertEqual(actual['enabledPlugins'][PLUGIN], enabled)
                self.assertEqual(actual['extraKnownMarketplaces']['other'], original['extraKnownMarketplaces']['other'])
                self.assertEqual(MERGE['merge_claude_settings'](output, enabled), output)

    def test_claude_enables_ruby_lsp_from_anthropics_marketplace_whatever_pstack_is(self):
        # Claude Code's built-in directory serves ruby-lsp without its language server.
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                switched_off = json.dumps({'enabledPlugins': {RUBY_LSP: False}})
                actual = json.loads(MERGE['merge_claude_settings'](switched_off, enabled))
                self.assertIs(actual['enabledPlugins'][RUBY_LSP], True)
                self.assertEqual(
                    actual['extraKnownMarketplaces']['claude-plugins-official'],
                    {'source': {'source': 'github', 'repo': 'anthropics/claude-plugins-official'}},
                )

    def test_codex_keeps_other_plugins_mcp_hooks_and_pstack_specific_options(self):
        source = (
            f'# local preferences\nmodel = "kept"\n[features]\nhooks = true\n'
            '[mcp_servers.other]\nurl = "http://localhost:9000"\n'
            '[plugins."other@official"]\nenabled = true\n'
            f'[plugins."{PLUGIN}"]\nenabled = false\n[plugins."{PLUGIN}".settings]\ncustom = "kept"\n'
            '[marketplaces.other]\nsource_type = "local"\nsource = "/other"\n'
        )
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                result = MERGE['merge_codex'](source, {}, enabled)
                actual, original = tomllib.loads(result), tomllib.loads(source)
                for key in ('model', 'features', 'mcp_servers'):
                    self.assertEqual(actual[key], original[key])
                self.assertEqual(actual['plugins']['other@official'], original['plugins']['other@official'])
                self.assertEqual(actual['plugins'][PLUGIN]['settings'], {'custom': 'kept'})
                self.assertEqual(actual['plugins'][PLUGIN]['enabled'], enabled)
                self.assertEqual(actual['marketplaces']['other'], original['marketplaces']['other'])
                self.assertEqual(actual['marketplaces'][MARKETPLACE]['ref'], 'main')
                self.assertIn('# local preferences', result)
                self.assertEqual(MERGE['merge_codex'](result, {}, enabled), result)

    def test_inline_plugin_settings_are_refused_before_installation(self):
        source = f'[plugins]\n"{PLUGIN}" = {{enabled = false}}\n'
        with self.assertRaisesRegex(ValueError, 'inline plugins'):
            MERGE['merge_codex'](source, {}, True)

    def test_retirement_removes_only_the_legacy_mcp_entry(self):
        source = '[mcp_servers.gbrain]\ncommand = "/old"\n[mcp_servers.zotero]\ncommand = "/kept"\n'
        output = MERGE['merge_codex'](source, {'gbrain': None}, True)
        self.assertNotIn('gbrain', tomllib.loads(output)['mcp_servers'])
        self.assertEqual(tomllib.loads(output)['mcp_servers']['zotero']['command'], '/kept')

    # Compound Engineering's retirement. Remove after 2027-01-29, with CE_PLUGIN in
    # scripts/merge-ai-config.py.
    def test_ce_entries_go_and_everything_else_stays(self):
        ce, ce_marketplace = MERGE['CE_PLUGIN'], MERGE['CE_MARKETPLACE']
        claude = {
            'enabledPlugins': {ce: True, 'other@official': True},
            'extraKnownMarketplaces': {
                ce_marketplace: {'source': {'source': 'github', 'repo': 'EveryInc/compound-engineering-plugin'}},
                'other': {'source': {'source': 'github', 'repo': 'org/other'}},
            },
        }
        output = MERGE['merge_claude_settings'](json.dumps(claude), True)
        actual = json.loads(output)
        self.assertEqual(actual['enabledPlugins'], {'other@official': True, PLUGIN: True, RUBY_LSP: True})
        self.assertEqual(set(actual['extraKnownMarketplaces']), {'other', MARKETPLACE, 'claude-plugins-official'})
        self.assertEqual(MERGE['merge_claude_settings'](output, True), output)
        codex = (
            f'[plugins."{ce}"]\nenabled = true\n[plugins."other@official"]\nenabled = true\n'
            f'[marketplaces.{ce_marketplace}]\nsource_type = "git"\nsource = "https://example.org/ce.git"\n'
        )
        output = MERGE['merge_codex'](codex, {}, True)
        actual = tomllib.loads(output)
        self.assertEqual(set(actual['plugins']), {'other@official', PLUGIN})
        self.assertEqual(set(actual['marketplaces']), {MARKETPLACE})
        self.assertEqual(MERGE['merge_codex'](output, {}, True), output)

    def test_installer_uninstalls_only_the_users_ce(self):
        ce, ce_marketplace = MERGE['CE_PLUGIN'], MERGE['CE_MARKETPLACE']
        project = {'scope': 'project', 'projectPath': '/work/project'}
        for installs, present in (([{'scope': 'user'}, project], True), ([project], False)):
            with self.subTest(installs=installs), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                registry = home / '.claude/plugins/installed_plugins.json'
                registry.parent.mkdir(parents=True)
                registry.write_text(json.dumps({'plugins': {ce: installs}}))
                record = home / '.local/state/shaffy/compound-engineering/installed.json'
                record.parent.mkdir(parents=True)
                record.write_text('{}')
                if present:
                    # Codex's downloaded copy, which its list no longer names once the table is gone.
                    (home / '.codex/.tmp/marketplaces' / ce_marketplace).mkdir(parents=True)
                with mock.patch.object(INSTALL['subprocess'], 'run') as run:
                    INSTALL['retire_compound_engineering'](home, {})
                # Each host's own command, after `mise exec TOOL --`.
                calls = [call.args[0][call.args[0].index('--') + 1 :] for call in run.call_args_list]
                expected = [['claude', 'plugin', 'uninstall', ce, '--scope', 'user']] if present else []
                expected += [['codex', 'plugin', 'remove', ce]]
                expected += [['codex', 'plugin', 'marketplace', 'remove', ce_marketplace]] if present else []
                self.assertEqual(calls, expected)
                self.assertFalse(record.parent.exists())

    def test_installer_requires_source_declared_configs_and_supports_opt_out(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with self.assertRaisesRegex(ValueError, 'Apply the declared'):
                INSTALL['declared'](home, True, {})
            for enabled in (True, False):
                for name, content in (
                    ('.claude/settings.json', MERGE['merge_claude_settings']('', enabled)),
                    ('.codex/config.toml', MERGE['merge_codex']('', {}, enabled)),
                ):
                    path = home / name
                    path.parent.mkdir(exist_ok=True)
                    path.write_text(content)
                INSTALL['declared'](home, enabled, {})
                with self.assertRaises(ValueError):
                    INSTALL['declared'](home, not enabled, {})

    def test_installer_accepts_the_bound_supplements_hooks(self):
        # The home apply merges the supplement's hooks into Claude's settings while one is bound.
        hooks = MERGE['bound_hooks']({'selection': {'mode': 'supplement'}})
        self.assertTrue(hooks)
        self.assertEqual(MERGE['bound_hooks']({'selection': {'mode': 'none'}}), {})
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            for name, content in (
                ('.claude/settings.json', MERGE['merge_claude_settings']('', True, hooks)),
                ('.codex/config.toml', MERGE['merge_codex']('', {}, True)),
            ):
                (home / name).parent.mkdir()
                (home / name).write_text(content)
            INSTALL['declared'](home, True, hooks)
            with self.assertRaisesRegex(ValueError, 'Apply the declared'):
                INSTALL['declared'](home, True, {})

    def test_refresh_adds_missing_marketplaces_before_installing_every_claude_plugin(self):
        both = {MARKETPLACE: {}, 'claude-plugins-official': {}}
        for known, added in (
            ({}, {'michael-denyer/pstack-claude', 'anthropics/claude-plugins-official'}),
            (both, set()),
        ):
            with self.subTest(known=known), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                for name, content in (
                    ('.claude/settings.json', MERGE['merge_claude_settings']('', True)),
                    ('.codex/config.toml', MERGE['merge_codex']('', {}, True)),
                    ('.claude/plugins/known_marketplaces.json', json.dumps(known)),
                ):
                    (home / name).parent.mkdir(parents=True, exist_ok=True)
                    (home / name).write_text(content)
                with mock.patch.object(INSTALL['subprocess'], 'run') as run:
                    INSTALL['refresh'](home, True, {}, INSTALL['settings_snapshot'](home), {})
                # Each host's own command, after `mise exec TOOL --`.
                calls = [call.args[0][call.args[0].index('--') + 1 :] for call in run.call_args_list]
                adds = [call for call in calls if call[:4] == ['claude', 'plugin', 'marketplace', 'add']]
                self.assertEqual({call[4] for call in adds}, added)
                for plugin in (PLUGIN, RUBY_LSP):
                    install = calls.index(['claude', 'plugin', 'install', plugin, '--scope', 'user'])
                    self.assertTrue(all(calls.index(add) < install for add in adds))

    def native_run(self, claude, codex, applied, fail):
        """The plugin managers and the home apply, as the refresh calls them."""

        def run(command, **kwargs):
            if command[0] == str(ROOT / 'bin/shaffy'):
                applied.append(command)
                self.assertEqual(kwargs['cwd'], ROOT)
                claude.write_text(MERGE['merge_claude_settings'](claude.read_text(), False))
                codex.write_text(MERGE['merge_codex'](codex.read_text(), {}, False))
            elif 'install' in command:
                claude.write_text(MERGE['merge_claude_settings'](claude.read_text(), True))
            elif 'add' in command:
                codex.write_text(MERGE['merge_codex'](codex.read_text(), {}, True))
                if fail:
                    raise subprocess.CalledProcessError(1, command)

        return run

    def test_refresh_reapplies_opt_in_after_success_and_native_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                claude = home / '.claude/settings.json'
                codex = home / '.codex/config.toml'
                claude.parent.mkdir()
                codex.parent.mkdir()
                claude.write_text(MERGE['merge_claude_settings']('{"permissions":{"deny":["Bash(sudo:*)"]}}', False))
                codex.write_text(MERGE['merge_codex']('model = "kept"\n', {}, False))
                registry = home / '.claude/plugins/known_marketplaces.json'
                registry.parent.mkdir()
                registry.write_text(json.dumps({MARKETPLACE: {}, 'claude-plugins-official': {}}))
                original = INSTALL['settings_snapshot'](home)
                applied = []
                native_run = self.native_run(claude, codex, applied, fail)
                with mock.patch.object(INSTALL['subprocess'], 'run', side_effect=native_run):
                    if fail:
                        with self.assertRaises(subprocess.CalledProcessError):
                            INSTALL['refresh'](home, False, {}, original, {})
                    else:
                        INSTALL['refresh'](home, False, {}, original, {})
                self.assertEqual(applied, [[str(ROOT / 'bin/shaffy'), 'apply', '--home', '--checkout', str(ROOT)]])
                self.assertEqual(INSTALL['settings_snapshot'](home), original)

    def test_refresh_rejects_unrelated_native_configuration_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            for name, content in (
                ('.claude/settings.json', MERGE['merge_claude_settings']('', False)),
                ('.codex/config.toml', MERGE['merge_codex']('', {}, False)),
            ):
                path = home / name
                path.parent.mkdir()
                path.write_text(content)
            original = INSTALL['settings_snapshot'](home)
            path = home / '.claude/settings.json'
            changed = json.loads(path.read_text())
            changed['permissions'] = {'allow': ['Bash(sudo:*)']}
            path.write_text(json.dumps(changed))
            with mock.patch.object(INSTALL['subprocess'], 'run') as run:
                with self.assertRaisesRegex(ValueError, 'unrelated settings'):
                    INSTALL['reconcile_settings'](home, False, {}, original)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
