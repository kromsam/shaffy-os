"""Deployed home files must not depend on one checkout's absolute path."""
import json
import os
import re
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import tomllib
import unittest

REPO = Path(__file__).resolve().parents[1]
HOME = REPO / 'home'
merge = runpy.run_path(str(REPO / 'scripts/merge-ai-config.py'))


class CodexMergeTests(unittest.TestCase):
    def test_repeated_rewrites_do_not_add_blank_lines(self):
        servers = {'zotero': {'command': 'z', 'args': ['serve'], 'env': {'WRITE': 'false'}},
                   'gbrain': {'command': 'g', 'args': []}}
        text = ('model = "x"\n\n[mcp_servers.zotero]\ncommand = "z"\nargs = ["serve"]\n\n'
                '[mcp_servers.zotero.env]\nWRITE = "true"\n\n[mcp_servers.gbrain]\ncommand = "g"\n'
                'args = []\n\n[projects."/p"]\ntrust_level = "trusted"\n')
        outputs = []
        for _ in range(3):
            text = merge['merge_codex'](text, servers)
            outputs.append(text)
            # The application flips the value back, forcing another rewrite.
            text = text.replace('WRITE = "false"', 'WRITE = "true"')
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])
        self.assertNotIn('\n\n\n', outputs[0])


class CheckoutPathTests(unittest.TestCase):
    def test_home_sources_never_name_a_checkout(self):
        # The whole path: a checkout at /work must not match ~/.local/share/workmux.
        checkout = re.compile(re.escape(os.fsencode(str(REPO))) + rb'(?![\w.-])')
        offenders = [str(path.relative_to(REPO)) for path in HOME.rglob('*')
                     if path.is_file() and '__pycache__' not in path.parts
                     and checkout.search(path.read_bytes())]
        self.assertEqual(offenders, [])

    def test_gbrain_launcher_runs_the_installed_proxy(self):
        launcher = (HOME / 'private_dot_local/bin/executable_gbrain-mcp').read_text()
        self.assertIn('"$HOME/.local/lib/shaffy/gbrain-mcp-proxy.py"', launcher)
        proxy = HOME / 'private_dot_local/lib/shaffy/gbrain-mcp-proxy.py'
        compile(proxy.read_text(), str(proxy), 'exec')

    def test_gbrain_reapply_script_installs_everything_the_launcher_runs(self):
        launcher = (HOME / 'private_dot_local/bin/executable_gbrain-mcp').read_text()
        script = (REPO / 'scripts/configure-gbrain-mcp.sh').read_text()
        # A direct chezmoi apply is refused; the home apply installs every managed target.
        self.assertIn('"$repo/bin/shaffy" apply --home', script)
        self.assertNotIn('chezmoi', script)
        for target in re.findall(r'"\$HOME/(\.local/lib/[^"]+)"', launcher):
            source = HOME / target.replace('.local/', 'private_dot_local/', 1)
            self.assertTrue(source.exists(), target)


@unittest.skipUnless(shutil.which('chezmoi'), 'chezmoi is not installed')
class ApplyingCheckoutTests(unittest.TestCase):
    """Render the AI configs from a copy of the source with a marker server."""

    def render(self, source_name, target, existing, supplement=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, dest = root / 'checkout', root / 'home'
            (source / 'scripts').mkdir(parents=True)
            shutil.copy(REPO / 'scripts/merge-ai-config.py', source / 'scripts')
            shutil.copytree(REPO / 'lib', source / 'lib', ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(REPO / 'schema', source / 'schema')
            (source / 'defaults').mkdir()
            (source / 'defaults/config.yaml').write_text(
                'schema_version: 1\nai:\n  mcp_servers:\n    marker:\n'
                '      command: "/only/in/this/checkout"\n      args: []\n')
            if supplement:
                (source / 'supplement').mkdir()
                (source / 'supplement/config.yaml').write_text(
                    'schema_version: 1\nsupplement_id: test\n' + supplement)
            shim = source / 'home' / source_name
            shim.parent.mkdir(parents=True)
            shutil.copy(HOME / source_name, shim)
            (dest / target).parent.mkdir(parents=True)
            (dest / target).write_text(existing)
            config = root / 'chezmoi.toml'
            config.write_text(f'persistentState = "{root / "state.boltdb"}"\n')
            result = subprocess.run(
                ['chezmoi', '--config', str(config), '--source', str(source / 'home'),
                 '--destination', str(dest), 'cat', str(dest / target)],
                capture_output=True, text=True, cwd=root,
                env={'HOME': str(dest), 'PATH': os.environ['PATH']}, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

    def test_codex_config_uses_this_checkouts_merge_and_servers(self):
        output = self.render('private_dot_codex/modify_private_config.toml',
                             '.codex/config.toml', 'model = "x"\n')
        self.assertIn('/only/in/this/checkout', output)
        self.assertIn('model = "x"', output)

    def test_claude_config_uses_this_checkouts_merge_and_servers(self):
        output = self.render('modify_private_dot_claude.json', '.claude.json', '{"theme": "dark"}')
        data = json.loads(output)
        self.assertEqual(data['mcpServers']['marker']['command'], '/only/in/this/checkout')
        self.assertEqual(data['theme'], 'dark')

    def test_supplement_can_disable_a_default_and_add_a_server_in_both_clients(self):
        supplement = ('ai:\n  mcp_servers:\n    marker:\n      enabled: false\n'
                      '    new:\n      enabled: true\n      command: "~/bin/new-mcp"\n')
        for source, target, existing, parse, key in (
                ('private_dot_codex/modify_private_config.toml', '.codex/config.toml',
                 'model="kept"\n[mcp_servers.marker]\ncommand="old"\n', tomllib.loads, 'mcp_servers'),
                ('modify_private_dot_claude.json', '.claude.json',
                 '{"theme":"dark","mcpServers":{"marker":{"command":"old"}}}', json.loads, 'mcpServers')):
            with self.subTest(target=target):
                output = self.render(source, target, existing, supplement)
                servers = parse(output)[key]
                self.assertNotIn('marker', servers)
                self.assertEqual(Path(servers['new']['command']).parts[-3:], ('home', 'bin', 'new-mcp'))
                self.assertNotIn('enabled', servers['new'])
                self.assertEqual(servers['new']['args'], [])

    def test_a_new_claude_file_with_the_default_server_disabled_is_valid_json(self):
        output = self.render('modify_private_dot_claude.json', '.claude.json', '',
                             'ai:\n  mcp_servers:\n    marker:\n      enabled: false\n')
        self.assertEqual(json.loads(output), {})


if __name__ == '__main__':
    unittest.main()
