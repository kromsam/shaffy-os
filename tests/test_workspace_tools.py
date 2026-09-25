"""Verify private-setting preservation and the workmux/tmux workspace sources."""
import json
from pathlib import Path
import runpy
import tomllib
import unittest

import yaml

REPO = Path(__file__).resolve().parents[1]
HOME = REPO / 'home'
merge = runpy.run_path(str(REPO / 'scripts/merge-ai-config.py'))


class WorkspaceToolsTests(unittest.TestCase):
    def test_multiple_existing_servers_preserve_formatting_without_blank_line_growth(self):
        servers = {'gbrain': {'command': '/gbrain', 'args': []},
                   'zotero': {'command': '/zotero', 'args': ['serve']}}
        original = 'model = "kept"\n\n[mcp_servers.gbrain]\ncommand="/gbrain"\nargs=[]\n\n[mcp_servers.zotero]\ncommand="/zotero"\nargs=["serve"]\n'
        self.assertEqual(merge['merge_codex'](original, servers), original)

    def test_codex_preserves_unrelated_settings_and_is_idempotent(self):
        source = '# existing preference\nmodel="kept"\n[mcp_servers.other]\nurl="http://localhost:1234"\n[mcp_servers.zotero]\ncommand="old"\n[mcp_servers.zotero.env]\nOLD="old"\n[projects."/some project"]\ntrust_level="trusted"\n'
        servers = {'zotero': {'command': '/path with spaces/server', 'args': ['serve'], 'env': {'ZOTERO_LOCAL': 'true'}}}
        output = merge['merge_codex'](source, servers)
        self.assertEqual(merge['merge_codex'](output, servers), output)
        expected = tomllib.loads(source)
        expected['mcp_servers'].update(servers)
        self.assertEqual(tomllib.loads(output), expected)
        self.assertIn('# existing preference', output)

    def test_inline_codex_entry_refuses_unreviewed_rewrite(self):
        with self.assertRaises(ValueError):
            merge['merge_codex']('[mcp_servers]\nzotero={command="old"}\n', {'zotero': {'command': 'new'}})

    def test_claude_preserves_account_and_other_servers(self):
        original = {'account': {'id': 'private'}, 'mcpServers': {'other': {'command': 'kept'}}}
        output = json.loads(merge['merge_claude'](json.dumps(original), {'zotero': {'command': 'new'}}))
        self.assertEqual(output['account'], original['account'])
        self.assertEqual(output['mcpServers']['other'], original['mcpServers']['other'])
        app_format = json.dumps(output, separators=(',', ':'))
        self.assertEqual(merge['merge_claude'](app_format, {'zotero': {'command': 'new'}}), app_format)

    def test_workmux_default_panes_follow_omarchy_tdl_geometry(self):
        config = yaml.safe_load((HOME / 'dot_config/workmux/config.yaml').read_text())
        self.assertEqual(config['agent'], 'claude')
        self.assertTrue(config['nerdfont'], 'the first run must not prompt for nerd font icons')
        editor, shell, agent = config['panes']
        self.assertEqual((editor['command'], editor['focus']), ('nvim .', True))
        self.assertEqual((shell['split'], shell['percentage']), ('vertical', 15))
        self.assertEqual((agent['command'], agent['split'], agent['percentage'], agent['target']),
                         ('<agent>', 'horizontal', 30, 0))

    def test_workmux_layouts_offer_codex_and_both_agents(self):
        config = yaml.safe_load((HOME / 'dot_config/workmux/config.yaml').read_text())
        codex = config['layouts']['codex']['panes']
        self.assertEqual([p.get('command') for p in codex], ['nvim .', None, 'codex'])
        duo = config['layouts']['duo']['panes']
        self.assertEqual([p.get('command') for p in duo], ['nvim .', None, 'claude', 'codex'])
        self.assertEqual((duo[3]['split'], duo[3]['target']), ('vertical', 2))
        for layout in config['layouts'].values():
            self.assertEqual(len([p for p in layout['panes'] if p.get('focus')]), 1)

    def test_workmux_is_pinned_through_mise_everywhere(self):
        pin = '"cargo:raine/workmux" = "tag:v0.1.264"'
        self.assertIn(pin, (HOME / 'dot_config/mise/config.toml').read_text())
        # The install script takes every version from that file: it keeps no copy of its own.
        script = (REPO / 'scripts/install-workspace-tools.sh').read_text()
        self.assertIn('home/dot_config/mise/config.toml', script)
        self.assertNotIn('@tag:v', script)
        self.assertNotIn('0.155.0', script)
        wrapper = (HOME / 'private_dot_local/bin/executable_workmux').read_text()
        self.assertIn('exec /usr/bin/mise exec -- workmux "$@"', wrapper)

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
