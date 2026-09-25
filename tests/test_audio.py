"""The yabridgectl configuration modifier (REAPER's Windows plugin bridge)."""
import json
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BridgeModifierTests(unittest.TestCase):
    def test_bridge_modifier_preserves_generated_cache_and_uses_real_option(self):
        source = 'yabridge_path="obsolete"\nplugin_dirs=[]\n[last_known_config]\nwine_version="kept"\nyabridge_host_hash=123\n'
        with tempfile.TemporaryDirectory() as directory:
            # Rendered for a synthetic home, as chezmoi does before running a modify_ script.
            home = Path(directory) / 'home tester'
            template = (ROOT / 'home/dot_config/yabridgectl/modify_config.toml.tmpl').read_text()
            modifier = Path(directory) / 'modifier.py'
            modifier.write_text(template.replace('{{ printf "%s/.wine/drive_c/Program Files/VSTPlugins" .chezmoi.homeDir | toJson }}',
                                                 json.dumps(f'{home}/.wine/drive_c/Program Files/VSTPlugins'))
                                        .replace('{{ printf "%s/.local/share/yabridge" .chezmoi.homeDir | toJson }}',
                                                 json.dumps(f'{home}/.local/share/yabridge')))
            result = subprocess.check_output(['python3', str(modifier)], input=source, text=True)
            parsed = tomllib.loads(result)
            self.assertEqual(subprocess.check_output(['python3', str(modifier)], input=result, text=True), result)
        self.assertNotIn('yabridge_path', parsed)
        self.assertEqual(parsed['yabridge_home'], f'{home}/.local/share/yabridge')
        self.assertEqual(parsed['plugin_dirs'], [f'{home}/.wine/drive_c/Program Files/VSTPlugins'])
        self.assertEqual(parsed['last_known_config'], {'wine_version': 'kept', 'yabridge_host_hash': 123})


if __name__ == '__main__':
    unittest.main()
