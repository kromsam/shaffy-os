"""Verify private-setting preservation and the workmux/tmux workspace sources."""

import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import tomllib
import unittest

import yaml

REPO = Path(__file__).resolve().parents[1]
HOME = REPO / 'home'
merge = runpy.run_path(str(REPO / 'scripts/merge-ai-config.py'))


class WorkspaceToolsTests(unittest.TestCase):
    def test_multiple_existing_servers_preserve_formatting_without_blank_line_growth(self):
        servers = {'gbrain': {'command': '/gbrain', 'args': []}, 'zotero': {'command': '/zotero', 'args': ['serve']}}
        original = 'model = "kept"\n\n[mcp_servers.gbrain]\ncommand="/gbrain"\nargs=[]\n\n[mcp_servers.zotero]\ncommand="/zotero"\nargs=["serve"]\n'
        self.assertEqual(merge['merge_codex'](original, servers), original)

    def test_codex_preserves_unrelated_settings_and_is_idempotent(self):
        source = '# existing preference\nmodel="kept"\n[mcp_servers.other]\nurl="http://localhost:1234"\n[mcp_servers.zotero]\ncommand="old"\n[mcp_servers.zotero.env]\nOLD="old"\n[projects."/some project"]\ntrust_level="trusted"\n'
        servers = {
            'zotero': {'command': '/path with spaces/server', 'args': ['serve'], 'env': {'ZOTERO_LOCAL': 'true'}}
        }
        output = merge['merge_codex'](source, servers)
        self.assertEqual(merge['merge_codex'](output, servers), output)
        expected = tomllib.loads(source)
        expected['mcp_servers'].update(servers)
        self.assertEqual(tomllib.loads(output), expected)
        self.assertIn('# existing preference', output)

    def test_inline_codex_entry_refuses_unreviewed_rewrite(self):
        with self.assertRaises(ValueError):
            merge['merge_codex']('[mcp_servers]\nzotero={command="old"}\n', {'zotero': {'command': 'new'}})

    def test_disabling_a_codex_server_removes_only_its_tables_and_converges(self):
        for header in ('zotero', '"zotero"', "'zotero'"):
            with self.subTest(header=header):
                source = (
                    '# preference\nmodel="kept"\n'
                    f'[mcp_servers.{header}]\ncommand="old"\n'
                    f'[mcp_servers.{header}.env]\nWRITE="false"\n'
                    '[mcp_servers.other]\nurl="http://localhost:1234"\n'
                    '[projects."/some project"]\ntrust_level="trusted"\n'
                )
                expected = tomllib.loads(source)
                del expected['mcp_servers']['zotero']
                output = merge['merge_codex'](source, {'zotero': None})
                self.assertEqual(tomllib.loads(output), expected)
                self.assertTrue(output.startswith('# preference\nmodel="kept"\n'))
                self.assertEqual(merge['merge_codex'](output, {'zotero': None}), output)

    def test_disabling_the_last_codex_server_handles_an_implicit_or_explicit_parent(self):
        for source, expected in (
            ('[mcp_servers.zotero]\ncommand="old"', {}),
            ('[mcp_servers]\n  [mcp_servers.zotero]\ncommand="old"', {'mcp_servers': {}}),
        ):
            with self.subTest(source=source):
                output = merge['merge_codex'](source, {'zotero': None})
                self.assertEqual(tomllib.loads(output), expected)
                self.assertEqual(merge['merge_codex'](output, {'zotero': None}), output)

    def test_disabling_an_inline_codex_entry_still_refuses_an_unreviewed_rewrite(self):
        with self.assertRaisesRegex(ValueError, 'Review the existing inline MCP entry for zotero'):
            merge['merge_codex']('[mcp_servers]\nzotero={command="old"}\n', {'zotero': None})

    def test_disabling_an_absent_server_changes_neither_clients_configuration(self):
        for client, sources in (
            ('codex', ['', '# preference\nmodel="kept"\n']),
            ('claude', ['{}', '{"account":{"id":"private"}}']),
        ):
            for source in sources:
                with self.subTest(client=client, source=source):
                    self.assertEqual(merge[f'merge_{client}'](source, {'zotero': None}), source)

    def test_a_fresh_claude_file_is_valid_json_when_every_server_is_disabled(self):
        output = merge['merge_claude']('', {'zotero': None})
        self.assertEqual(json.loads(output), {})
        self.assertEqual(merge['merge_claude'](output, {'zotero': None}), output)

    def test_disabling_a_claude_server_preserves_accounts_and_other_servers(self):
        original = {
            'account': {'id': 'private'},
            'mcpServers': {'other': {'command': 'kept'}, 'zotero': {'command': 'old'}},
        }
        output = merge['merge_claude'](json.dumps(original), {'zotero': None})
        del original['mcpServers']['zotero']
        self.assertEqual(json.loads(output), original)
        self.assertEqual(merge['merge_claude'](output, {'zotero': None}), output)

    def test_claude_preserves_account_and_other_servers(self):
        original = {'account': {'id': 'private'}, 'mcpServers': {'other': {'command': 'kept'}}}
        output = json.loads(merge['merge_claude'](json.dumps(original), {'zotero': {'command': 'new'}}))
        self.assertEqual(output['account'], original['account'])
        self.assertEqual(output['mcpServers']['other'], original['mcpServers']['other'])
        app_format = json.dumps(output, separators=(',', ':'))
        self.assertEqual(merge['merge_claude'](app_format, {'zotero': {'command': 'new'}}), app_format)

    def test_supplement_hooks_follow_every_other_hook_and_leave_them_alone(self):
        workmux = {'hooks': [{'type': 'command', 'command': 'workmux set-window-status done'}]}
        original = json.dumps({'theme': 'light', 'hooks': {'Stop': [workmux]}}, indent=2) + '\n'
        hooks = merge['supplement_hooks'](Path('/checkout'))
        merged = merge['merge_claude_settings'](original, True, hooks)
        settings = json.loads(merged)
        self.assertEqual(settings['theme'], 'light')
        # Appended: the existing hooks keep their places, which Codex's trust records name.
        self.assertEqual(settings['hooks']['Stop'], [workmux, hooks['Stop']])
        self.assertEqual(settings['hooks']['PostToolUse'], [hooks['PostToolUse']])
        command = '"/checkout/bin/shaffy" lint --hook --supplement'
        self.assertEqual(hooks['PostToolUse']['hooks'][0]['command'], command)
        self.assertEqual(merge['merge_claude_settings'](merged, True, hooks), merged)
        # Another checkout replaces the entries; unbinding the supplement removes them.
        moved = json.loads(merge['merge_claude_settings'](merged, True, merge['supplement_hooks'](Path('/moved'))))
        self.assertEqual(len(moved['hooks']['Stop']), 2)
        unbound = json.loads(merge['merge_claude_settings'](merged, True, {}))
        self.assertEqual(unbound['hooks'], {'Stop': [workmux]})

    def test_codex_gets_the_supplement_hooks_for_its_patches(self):
        merged = json.loads(merge['merge_codex_hooks']('', merge['supplement_hooks'](Path('/c'), codex=True)))
        self.assertEqual(merged['hooks']['PostToolUse'][0]['matcher'], '^(apply_patch|Edit|Write)$')
        self.assertEqual(merge['merge_codex_hooks']('', {}), '')

    def test_workmux_default_panes_follow_omarchy_tdl_geometry(self):
        config = yaml.safe_load((HOME / 'dot_config/workmux/config.yaml').read_text())
        self.assertEqual(config['agent'], 'claude')
        self.assertTrue(config['nerdfont'], 'the first run must not prompt for nerd font icons')
        editor, shell, agent = config['panes']
        self.assertEqual((editor['command'], editor['focus']), ('nvim .', True))
        self.assertEqual((shell['split'], shell['percentage']), ('vertical', 15))
        self.assertEqual(
            (agent['command'], agent['split'], agent['percentage'], agent['target']), ('<agent>', 'horizontal', 30, 0)
        )

    def test_workmux_layouts_offer_codex_and_both_agents(self):
        config = yaml.safe_load((HOME / 'dot_config/workmux/config.yaml').read_text())
        codex = config['layouts']['codex']['panes']
        self.assertEqual([p.get('command') for p in codex], ['nvim .', None, 'codex'])
        duo = config['layouts']['duo']['panes']
        self.assertEqual([p.get('command') for p in duo], ['nvim .', None, 'claude', 'codex'])
        self.assertEqual((duo[3]['split'], duo[3]['target']), ('vertical', 2))
        for layout in config['layouts'].values():
            self.assertEqual(len([p for p in layout['panes'] if p.get('focus')]), 1)

    def test_workspace_tools_use_latest_through_mise_everywhere(self):
        config = tomllib.loads((HOME / 'dot_config/mise/config.toml').read_text())
        tools = config['tools']
        # A tool with options is a table; its version follows latest as well.
        self.assertTrue(
            all((tool['version'] if isinstance(tool, dict) else tool) == 'latest' for tool in tools.values())
        )
        # `latest` waits for releases to age, so a withdrawn release is not installed.
        self.assertRegex(config['settings']['minimum_release_age'], r'^[1-9][0-9]*d$')
        self.assertIn('github:raine/workmux', tools)
        # The install script takes every tool from that file: it keeps no list or version of its own.
        script = (REPO / 'scripts/install-workspace-tools.sh').read_text()
        self.assertIn('home/dot_config/mise/config.toml', script)
        self.assertNotIn('workmux', script)
        self.assertNotIn('@', script.replace('"${tools[@]}"', ''))
        # Zotero MCP is a mise tool too; the script only removes the copy uv installed before.
        self.assertIn('pypi:zotero-mcp-server', tools)
        self.assertNotIn('uv tool install', script)
        self.assertIn('uv tool uninstall zotero-mcp-server', script)
        wrapper = (HOME / 'private_dot_local/bin/executable_workmux').read_text()
        self.assertIn('exec /usr/bin/mise exec -- workmux "$@"', wrapper)

    def test_tools_install_only_once_the_home_has_this_checkouts_configuration(self):
        # mise takes options from the home's configuration; a tool missing there installs without them.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bin').mkdir()
            for name in ('mise', 'uv'):
                (root / 'bin' / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{root}/calls"\n')
                (root / 'bin' / name).chmod(0o755)
            (root / 'config/mise').mkdir(parents=True)
            (root / 'config/mise/config.toml').write_text('[tools]\nclaude = "latest"\n')
            result = subprocess.run(
                ['bash', str(REPO / 'scripts/install-workspace-tools.sh')],
                env={
                    **os.environ,
                    'XDG_CONFIG_HOME': str(root / 'config'),
                    'PATH': f'{root / "bin"}:{os.environ["PATH"]}',
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('run ./bin/shaffy apply --home first', result.stderr)
            self.assertFalse((root / 'calls').exists())

    def test_launchers_run_installed_tools_without_version_lookups(self):
        # `mise exec TOOL@latest` resolves every configured `latest` against upstream and,
        # with mise's default exec_auto_install, installs whatever is newer first.
        sources = [
            *sorted((HOME / 'private_dot_local/bin').glob('executable_*')),
            REPO / 'scripts/install-workspace-tools.sh',
        ]
        for path in sources:
            for line in path.read_text().splitlines():
                if 'mise exec ' in line:
                    tools = (' ' + line.partition('mise exec ')[2]).partition(' -- ')[0]
                    self.assertNotIn('@', tools, f'{path.name}: {line}')
        browser = (HOME / 'private_dot_local/bin/executable_agent-browser').read_text()
        self.assertIn('exec /usr/bin/mise exec npm:agent-browser -- agent-browser ', browser)

    def test_tmux_configuration_matches_omarchy_prefix_and_bar(self):
        conf = (HOME / 'dot_config/tmux/tmux.conf').read_text()
        self.assertIn('set -g prefix C-Space', conf)
        self.assertIn('set -g status-position top', conf)
        self.assertIn('bind -N "Split pane vertically" -n M-Enter', conf)
        self.assertNotIn('omarchy-', conf, 'Omarchy helper scripts are not installed here')

    def test_zellij_is_retired_from_sources_packages_and_deployed_home(self):
        packages = (REPO / 'defaults/packages/desktop_apps.yml').read_text().split()
        self.assertIn('tmux', packages)
        self.assertNotIn('zellij', packages)
        for group in (REPO / 'defaults/packages').glob('*.yml'):
            self.assertNotIn('zellij', group.read_text().split(), group.name)
        self.assertFalse((HOME / 'dot_config/zellij').exists())
        self.assertFalse((HOME / 'private_dot_local/bin/executable_tdl').exists())
        self.assertNotIn('zellij', (HOME / 'dot_config/noctalia/92-app-themes.toml').read_text())


if __name__ == '__main__':
    unittest.main()
