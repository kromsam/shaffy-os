"""CE configuration must converge without changing other agent capabilities."""

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
INSTALL = runpy.run_path(str(ROOT / 'scripts/install-compound-engineering.py'))
PLUGIN = MERGE['CE_PLUGIN']
MARKETPLACE = MERGE['CE_MARKETPLACE']


class CompoundEngineeringTests(unittest.TestCase):
    def test_claude_preserves_hooks_permissions_and_other_plugins(self):
        original = {
            'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'workmux status'}]}]},
            'permissions': {'deny': ['Bash(sudo:*)']},
            'enabledPlugins': {'ruby-lsp@official': True},
            'extraKnownMarketplaces': {'other': {'source': {'source': 'github', 'repo': 'org/other'}}},
        }
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                output = MERGE['merge_claude_settings'](json.dumps(original), enabled)
                actual = json.loads(output)
                for key in ('hooks', 'permissions'):
                    self.assertEqual(actual[key], original[key])
                self.assertTrue(actual['enabledPlugins']['ruby-lsp@official'])
                self.assertEqual(actual['enabledPlugins'][PLUGIN], enabled)
                self.assertEqual(actual['extraKnownMarketplaces']['other'], original['extraKnownMarketplaces']['other'])
                self.assertEqual(MERGE['merge_claude_settings'](output, enabled), output)

    def test_codex_keeps_other_plugins_mcp_hooks_and_ce_specific_options(self):
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

    def test_installer_requires_source_declared_configs_and_supports_opt_out(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with self.assertRaisesRegex(ValueError, 'Apply the declared'):
                INSTALL['declared'](home, True)
            for enabled in (True, False):
                for name, content in (
                    ('.claude/settings.json', MERGE['merge_claude_settings']('', enabled)),
                    ('.codex/config.toml', MERGE['merge_codex']('', {}, enabled)),
                ):
                    path = home / name
                    path.parent.mkdir(exist_ok=True)
                    path.write_text(content)
                INSTALL['declared'](home, enabled)
                with self.assertRaises(ValueError):
                    INSTALL['declared'](home, not enabled)

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
                registry.write_text(json.dumps({MARKETPLACE: {}}))
                original = INSTALL['settings_snapshot'](home)
                applied = []

                def native_run(command, **kwargs):
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

                with mock.patch.object(INSTALL['subprocess'], 'run', side_effect=native_run):
                    if fail:
                        with self.assertRaises(subprocess.CalledProcessError):
                            INSTALL['refresh'](home, False, original, {})
                    else:
                        INSTALL['refresh'](home, False, original, {})
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
                    INSTALL['reconcile_settings'](home, False, original)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
