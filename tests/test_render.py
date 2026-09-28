"""Render the managed home for a synthetic user and validate it."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from shaffy import sandbox  # noqa: E402
from shaffy.sandbox import RenderError, render  # noqa: E402

# The public Git defaults every rendered ~/.config/git/config ends with.
GIT_DEFAULTS = ('[init]\n    defaultBranch = main\n'
                "# Omarchy's Git defaults: rebase local commits on pull, track the remote branch on\n"
                '# the first push, clearer diffs and remembered conflict resolutions.\n'
                '[pull]\n    rebase = true\n[push]\n    autoSetupRemote = true\n'
                '[diff]\n    algorithm = histogram\n    colorMoved = plain\n    mnemonicPrefix = true\n'
                '[rerere]\n    enabled = true\n[branch]\n    sort = -committerdate\n'
                '[tag]\n    sort = -version:refname\n[column]\n    ui = auto\n'
                '[commit]\n    verbose = true\n')

DIRECT = os.environ.get('SHAFFY_RENDER_DIRECT') == '1'
TOOLS = ('chezmoi', 'niri', 'noctalia', 'fish', 'xdg-user-dir')


def _unavailable():
    missing = [tool for tool in TOOLS if shutil.which(tool) is None]
    if missing:
        return 'missing ' + ', '.join(missing)
    if DIRECT:
        return ''
    if shutil.which('bwrap') is None:
        return 'missing bwrap'
    probe = subprocess.run(['bwrap', '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc',
                            '--unshare-all', '--new-session', '--uid', '4242', '--', 'true'],
                           capture_output=True, check=False)
    return '' if probe.returncode == 0 else 'user namespaces are unavailable for bubblewrap'


REASON = _unavailable()


@unittest.skipIf(REASON, f'render sandbox unavailable: {REASON}')
class RenderTests(unittest.TestCase):
    def assertClean(self, result):
        self.assertEqual(result['problems'], [])
        self.assertTrue(result['converged'])
        self.assertGreaterEqual(result['files'], 80)  # the managed home; plugins come from Noctalia

    def test_public_defaults_render_validate_and_converge(self):
        result = render(ROOT, without=True)
        self.assertClean(result)
        if not DIRECT:
            self.assertEqual(result['identity'], '/home/tester|tester|4242|synthetic-host')

    def test_minimal_example_renders_like_the_defaults(self):
        self.assertClean(render(ROOT, supplement=ROOT / 'supplement.example'))

    def test_public_defaults_preserve_installer_directories_in_any_language(self):
        import tomllib
        for pictures, documents, downloads, videos in (
                ('Images', 'Documents personnels', 'Téléchargements', "Vidéos d'été"),
                ('画像 & 100%', '書類', 'ダウンロード', '動画')):
            with self.subTest(pictures=pictures), tempfile.TemporaryDirectory() as keep:
                native = ('# Installer-owned; keep comments and unrelated entries\n'
                          f'XDG_PICTURES_DIR="$HOME/{pictures}"\n'
                          f'XDG_DOCUMENTS_DIR="$HOME/{documents}"\n'
                          f'XDG_DOWNLOAD_DIR="$HOME/{downloads}"\n'
                          f'XDG_VIDEOS_DIR="$HOME/{videos}"\n'
                          'XDG_MUSIC_DIR="$HOME/"\nXDG_TEMPLATES_DIR="/srv/templates"\n')
                self.assertClean(render(ROOT, without=True, keep=keep, user_dirs=native))
                home = Path(keep) / 'home'
                prefix = str(home) if DIRECT else '/home/tester'
                self.assertEqual((home / '.config/user-dirs.dirs').read_text(), native)
                self.assertEqual((home / '.config/user-dirs.dirs').stat().st_mode & 0o777, 0o644)
                omarchy = tomllib.loads((home / '.config/noctalia/90-omarchy.toml').read_text())
                apps = tomllib.loads((home / '.config/noctalia/95-desktop-apps.toml').read_text())
                self.assertEqual(omarchy['shell']['screenshot']['directory'], f'{prefix}/{pictures}/Screenshots')
                self.assertEqual(apps['plugin_settings']['rylos/tailnet']['taildrop_dir'], f'{prefix}/{downloads}')
                self.assertEqual(apps['plugin_settings']['ahmedhossamdev/sticky-notes']['save_path'],
                                 f'{prefix}/{documents}/sticky-notes')
                self.assertEqual(apps['plugin_settings']['noctalia/screen_recorder']['directory'],
                                 f'{prefix}/{videos}/Recordings')
                niri = (home / '.config/niri/cfg/misc.kdl').read_text()
                self.assertIn(pictures.replace('%', '%%') + '/Screenshots/', niri)
                for name in ('Desktop', 'Documents', 'Downloads', 'Music', 'Pictures', 'Projects',
                             'Public', 'Templates', 'Videos', 'Work', '.hidden'):
                    self.assertFalse(os.path.lexists(home / name), name)

    def test_partial_folder_overrides_keep_the_other_installer_entries(self):
        native = ('# Installer choice\nXDG_PICTURES_DIR="$HOME/Images"\n'
                  'XDG_DOCUMENTS_DIR="$HOME/Documents personnels"\n'
                  'XDG_DOWNLOAD_DIR="$HOME/Téléchargements"\nXDG_MUSIC_DIR="$HOME/"\n')
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive',
                                    keep=keep, user_dirs=native))
            home = Path(keep) / 'home'
            expected = native.replace('$HOME/Images', '$HOME/Bilder') + 'XDG_VIDEOS_DIR="$HOME/Filme"\n'
            self.assertEqual((home / '.config/user-dirs.dirs').read_text(), expected)
            apps = __import__('tomllib').loads((home / '.config/noctalia/95-desktop-apps.toml').read_text())
            prefix = str(home) if DIRECT else '/home/tester'
            self.assertEqual(apps['plugin_settings']['ahmedhossamdev/sticky-notes']['save_path'],
                             f'{prefix}/Documents personnels/sticky-notes')

    def test_niri_screenshot_path_escapes_native_quotes_and_strftime_percent(self):
        native = 'XDG_PICTURES_DIR="$HOME/Photos \\"été\\" & 100%"\n'
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, without=True, keep=keep, user_dirs=native))
            niri = (Path(keep) / 'home/.config/niri/cfg/misc.kdl').read_text()
            self.assertIn('Photos \\"été\\" & 100%%/Screenshots/', niri)

    def test_standard_compatibility_links_need_no_folder_overrides(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as keep:
            supplement = Path(directory)
            (supplement / 'config.yaml').write_text(
                'schema_version: 1\nsupplement_id: aliases\nfolder_links:\n  Pictures: pictures\n')
            self.assertClean(render(ROOT, supplement=supplement, keep=keep,
                                    user_dirs='XDG_PICTURES_DIR="$HOME/Images"\n'))
            home = Path(keep) / 'home'
            self.assertEqual(os.readlink(home / 'Pictures'), 'Images')
            self.assertEqual((home / '.config/user-dirs.dirs').read_text(),
                             'XDG_PICTURES_DIR="$HOME/Images"\n')

    def test_supplement_settings_reach_the_rendered_files(self):
        with tempfile.TemporaryDirectory() as keep:
            result = render(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive',
                            keep=keep)
            self.assertClean(result)
            home = Path(keep) / 'home'
            codex = (home / '.codex/config.toml').read_text()
            self.assertIn('[mcp_servers.notes]', codex)
            self.assertIn('ZOTERO_LOCAL_WRITE = "true"', codex)
            self.assertIn(f'command = "{home if DIRECT else "/home/tester"}/.local/bin/example-notes-mcp"',
                          codex)
            display = (home / '.config/niri/cfg/display.kdl').read_text()
            self.assertIn('output "DP-9" {\n    mode "2560x1440@59.951"\n    scale 1.25\n'
                          '    position x=0 y=0\n}', display)
            self.assertIn('output "HDMI-A-7" {\n}', display)
            keyboard = (home / '.config/niri/cfg/input.kdl').read_text()
            self.assertIn('layout "us,de"\n            variant "intl,"\n'
                          '            options "grp:alt_shift_toggle"', keyboard)

    def test_folder_and_wallpaper_settings_reach_noctalia(self):
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive',
                                    keep=keep))
            home = Path(keep) / 'home'
            prefix = str(home) if DIRECT else '/home/tester'
            omarchy = (home / '.config/noctalia/90-omarchy.toml').read_text()
            self.assertIn(f'directory = "{prefix}/Bilder/Screenshots"', omarchy)
            self.assertIn(f'directory = "{prefix}/Wallpapers"', omarchy)
            self.assertIn('path = "/usr/share/backgrounds/example.png"', omarchy)
            apps = (home / '.config/noctalia/95-desktop-apps.toml').read_text()
            self.assertIn(f'directory = "{prefix}/Filme/Recordings"', apps)

    def test_password_managers_and_credential_prompts_are_blocked_from_capture(self):
        import re
        rules = (ROOT / 'home/dot_config/niri/cfg/rules.kdl').read_text()
        rule = re.search(r'match app-id=r#"([^"]+)"#\s*block-out-from "screen-capture"', rules)
        self.assertIsNotNone(rule)
        pattern = re.compile(rule[1])
        for app_id in ('Bitwarden', '1Password', 'org.keepassxc.KeePassXC', 'gcr-prompter',
                       'pinentry-qt', 'org.gnupg.pinentry-qt', 'org.gnome.World.Secrets'):
            self.assertRegex(app_id, pattern)
        for app_id in ('Alacritty', 'zen', 'bitwarden-login', 'org.gnome.Nautilus'):
            self.assertNotRegex(app_id, pattern)

    def test_noctalia_public_defaults_skip_the_wizard_and_lock_when_idle(self):
        import tomllib
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, without=True, keep=keep))
            home = Path(keep) / 'home'
            omarchy = tomllib.loads((home / '.config/noctalia/90-omarchy.toml').read_text())
            keybinds = (home / '.config/niri/cfg/keybinds.kdl').read_text()
        self.assertIs(omarchy['shell']['setup_wizard_enabled'], False)
        self.assertIs(omarchy['shell']['polkit_agent'], True)
        behaviors = omarchy['idle']['behavior']
        self.assertEqual((behaviors['lock']['action'], behaviors['lock']['timeout']), ('lock', 300))
        self.assertEqual(behaviors['screen-off']['action'], 'screen_off')
        self.assertGreater(behaviors['screen-off']['timeout'], behaviors['lock']['timeout'])
        self.assertTrue(all(behavior['enabled'] for behavior in behaviors.values()))
        self.assertNotIn('suspend', behaviors)  # a laptop that suspends by itself loses work
        self.assertIn('Mod+Ctrl+I', keybinds)
        self.assertIn('"caffeine-toggle"', keybinds)

    def test_overrides_are_rendered_last_and_win(self):
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive',
                                    keep=keep))
            home = Path(keep) / 'home'
            self.assertIn('Mod+F12', (home / '.config/niri/user.kdl').read_text())
            self.assertTrue((home / '.config/niri/config.kdl').read_text().rstrip()
                            .endswith('include "user.kdl"'))
            self.assertIn('top_center', (home / '.config/noctalia/97-supplement.toml').read_text())
            imports = __import__('tomllib').loads(
                (home / '.config/alacritty/alacritty.toml').read_text())['general']['import']
            self.assertEqual(imports, ['~/.config/alacritty/themes/noctalia.toml',
                                       '~/.config/alacritty/shaffy.toml',
                                       '~/.config/alacritty/supplement.toml'])
            self.assertIn('size = 11.0', (home / '.config/alacritty/supplement.toml').read_text())
            # fish reads conf.d first and config.fish last; the supplement must win over both.
            greeting = subprocess.run(
                ['fish', '-c', 'fish_greeting'], capture_output=True, text=True, timeout=30,
                env={'HOME': str(home), 'XDG_CONFIG_HOME': str(home / '.config'),
                     'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, check=False)
            self.assertIn('synthetic-supplement-greeting', greeting.stdout)

    def test_without_overrides_the_override_files_exist_and_are_empty(self):
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, without=True, keep=keep))
            home = Path(keep) / 'home'
            for target in ('.config/niri/user.kdl', '.config/noctalia/97-supplement.toml',
                           '.config/fish/supplement.fish', '.config/alacritty/supplement.toml'):
                self.assertEqual((home / target).read_text(), '', target)
            # The public wallpaper is the one the noctalia package installs; none is redistributed.
            omarchy = (home / '.config/noctalia/90-omarchy.toml').read_text()
            self.assertEqual(omarchy.count('"/usr/share/noctalia/assets/noctalia-wallpaper.png"'), 3)
            self.assertFalse((home / '.local/share/wallpapers').exists())

    def test_identity_folders_and_links_render_from_the_settings(self):
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive',
                                    keep=keep))
            home = Path(keep) / 'home'
            self.assertEqual((home / '.config/git/config').read_text(),
                             '[user]\n    name = Test Person\n    email = test@example.invalid\n'
                             '    signingkey = /etc/os-release\n' + GIT_DEFAULTS
                             + '    gpgsign = true\n[gpg]\n    format = ssh\n')
            dirs = (home / '.config/user-dirs.dirs').read_text()
            self.assertIn('XDG_PICTURES_DIR="$HOME/Bilder"', dirs)
            self.assertNotIn('XDG_DOCUMENTS_DIR=', dirs)
            self.assertNotIn('XDG_PROJECTS_DIR=', dirs)
            self.assertEqual(os.readlink(home / 'Pictures'), 'Bilder')
            self.assertEqual(os.readlink(home / 'Work'), 'Projects')
            self.assertFalse(os.path.lexists(home / 'Documents'))
            self.assertEqual((home / '.hidden').read_text(), 'Pictures\nWork\n')

    def test_assets_are_copied_from_the_supplement(self):
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive',
                                    keep=keep))
            home = Path(keep) / 'home'
            self.assertEqual((home / '.local/share/fonts/Example-Regular.otf').read_text(),
                             'OTTO synthetic font bytes\n')
            launcher = home / '.local/bin/example-launcher'
            self.assertEqual(oct(launcher.stat().st_mode & 0o777), '0o755')
            self.assertEqual(oct((home / '.config/example/token-free.conf').stat().st_mode & 0o777), '0o600')
            self.assertIn('node = "24.0.0"', (home / '.config/mise/conf.d/90-supplement.toml').read_text())
            photo = home / 'Wallpapers/example photo.jpg'
            self.assertFalse(photo.is_symlink())
            self.assertEqual(photo.read_text(), 'synthetic picture bytes\n')

    def test_an_asset_under_an_ignored_target_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            supplement = Path(directory) / 'supplement'
            shutil.copytree(ROOT / 'tests/fixtures/supplements/comprehensive', supplement)
            config = supplement / 'config.yaml'
            config.write_text(config.read_text().replace(
                '  "~/Wallpapers/example photo.jpg"', '  "~/.config/Zettlr/example photo.jpg"'))  # zettlr is disabled
            problems = render(ROOT, supplement=supplement)['problems']
            self.assertIn('assets.~/.config/Zettlr/example photo.jpg: its target is ignored here', '\n'.join(problems))

    def test_account_endpoints_and_writing_settings_render_only_when_set(self):
        preset = '.config/Zettlr/defaults/Custom - DOCX - standard.yaml'
        with tempfile.TemporaryDirectory() as keep, tempfile.TemporaryDirectory() as directory:
            supplement = Path(directory) / 'supplement'
            shutil.copytree(ROOT / 'tests/fixtures/supplements/comprehensive', supplement)
            config = supplement / 'config.yaml'
            config.write_text(config.read_text().replace('  zettlr: disabled', '  zettlr: managed'))
            self.assertClean(render(ROOT, supplement=supplement, keep=keep))
            home = Path(keep) / 'home'
            self.assertIn('admin_url = "https://headscale.example.invalid"',
                          (home / '.config/noctalia/95-desktop-apps.toml').read_text())
            login = (home / '.local/bin/bitwarden-login').read_text()
            self.assertIn("bw\" config server 'https://vault.example.invalid'", login)
            self.assertNotIn('noctalia', login)  # its launcher plugin is disabled
            self.assertIn(f'screenshot-path "{home if DIRECT else "/home/tester"}/Bilder/Screenshots/',
                          (home / '.config/niri/cfg/misc.kdl').read_text())
            docx = (home / preset).read_text()
            self.assertIn('  lang: en-GB', docx)
            self.assertIn('reference-doc: "${HOME}/Templates/reference.docx"', docx)
            csl = '.local/share/pandoc/csl/chicago-notes-bibliography-subsequent-ibid-no-url.csl'
            self.assertEqual((home / csl).read_text(),
                             (ROOT / 'home/private_dot_local/private_share/zotero-data/styles'
                              / 'chicago-notes-bibliography-subsequent-ibid-no-url.csl').read_text())
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, without=True, keep=keep))
            home = Path(keep) / 'home'
            self.assertNotIn('admin_url', (home / '.config/noctalia/95-desktop-apps.toml').read_text())
            self.assertNotIn('config server', (home / '.local/bin/bitwarden-login').read_text())
            self.assertIn(f'screenshot-path "{home if DIRECT else "/home/tester"}/Screenshots/',
                          (home / '.config/niri/cfg/misc.kdl').read_text())
            self.assertTrue((home / '.local/share/pandoc/csl').is_dir())

    def test_integrations_render_only_while_managed(self):
        wants = '.config/systemd/user/default.target.wants/syncthing.service'
        socket = '.config/systemd/user/sockets.target.wants/ssh-agent.socket'
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive',
                                    keep=keep))
            home = Path(keep) / 'home'
            self.assertEqual(os.readlink(home / wants), '/usr/lib/systemd/user/syncthing.service')
            self.assertEqual(os.readlink(home / socket), '/usr/lib/systemd/user/ssh-agent.socket')
            self.assertTrue((home / '.config/autostart/Nextcloud.desktop').is_file())
            self.assertTrue((home / '.ssh/config').is_file())
            # The agent environment follows the ssh integration that enables its socket.
            self.assertIn('SSH_AUTH_SOCK', (home / '.config/environment.d/30-ssh-agent.conf').read_text())
            self.assertIn('SSH_AUTH_SOCK', (home / '.config/fish/conf.d/30-ssh-agent.fish').read_text())
            self.assertIn('SSH_AUTH_SOCK', (home / '.config/niri/cfg/misc.kdl').read_text())
            self.assertIn('Path=shaffy.default\n', (home / '.zotero/zotero/profiles.ini').read_text())
            prefs = (home / '.zotero/zotero/shaffy.default/user.js').read_text()
            self.assertIn(f'"extensions.zotero.dataDir", "{home if DIRECT else "/home/tester"}/.local/share/'
                          'zotero-data"', prefs)
            # A fresh home has no REAPER or Zettlr configuration for their modifiers to change,
            # and the fixture disables zettlr, so none of its files render.
            self.assertFalse((home / '.config/REAPER/reaper.ini').exists())
            self.assertFalse((home / '.config/Zettlr/custom.css').exists())
            # Backup settings configure the client; its timers wait for the integration.
            backup = json.loads((home / '.config/restic-home/backup.json').read_text())
            self.assertEqual(backup, {'home': str(home) if DIRECT else '/home/tester', 'host': 'synthetic-laptop',
                                      'tags': ['automatic', 'shaffy'], 'repository': 'sftp:restic-home-nas:repository',
                                      'repository_id': '0123456789abcdef' * 4,
                                      'retention': {'daily': 7, 'weekly': 8, 'monthly': 12}})
            self.assertIn('    HostName backup.example.invalid\n    Port 2222\n    User restic-test\n',
                          (home / '.config/restic-home/ssh_config').read_text())
            self.assertTrue((home / '.config/restic-home/known_hosts').read_text().startswith(
                '[backup.example.invalid]:2222 ssh-ed25519 AAAA'))
            self.assertTrue((home / '.config/restic-home/excludes').read_text().endswith(
                '# From the settings.\n$HOME/Projects/scratch data\n'))
            self.assertTrue((home / '.config/systemd/user/restic-home-backup.timer').is_file())
            self.assertFalse(os.path.lexists(home / '.config/systemd/user/timers.target.wants/restic-home-backup.timer'))
            # The services may write only there, so it must exist before a timer first runs them.
            receipts = home / '.local/state/shaffy/home-backup'
            self.assertEqual((receipts.is_dir(), receipts.stat().st_mode & 0o777, list(receipts.iterdir())),
                             (True, 0o700, []))
            self.assertIn(' %h/.local/state/shaffy/home-backup',
                          (home / '.config/systemd/user/restic-home-backup.service').read_text())
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, without=True, keep=keep))
            home = Path(keep) / 'home'
            for target in (wants, socket, '.config/autostart/Nextcloud.desktop', '.ssh/config',
                           '.config/Zettlr/custom.css', '.zotero', '.local/share/zotero-data',
                           '.config/restic-home', '.local/bin/restic-home', '.local/state/shaffy/home-backup',
                           '.config/systemd/user/restic-home-backup.service',
                           '.config/environment.d/30-ssh-agent.conf', '.config/fish/conf.d/30-ssh-agent.fish'):
                self.assertFalse(os.path.lexists(home / target), target)
            self.assertNotIn('SSH_AUTH_SOCK', (home / '.config/niri/cfg/misc.kdl').read_text())

    def test_defaults_leave_outputs_and_keyboard_to_niri_and_localectl(self):
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, without=True, keep=keep))
            home = Path(keep) / 'home'
            self.assertNotIn('output "', (home / '.config/niri/cfg/display.kdl').read_text())
            self.assertNotIn('layout "', (home / '.config/niri/cfg/input.kdl').read_text())
            keybinds = (home / '.config/niri/cfg/keybinds.kdl').read_text()
            self.assertNotRegex(keybinds, r'(?<![\w.-])/home/(?!tester(?:/|"))')  # no other account's home
            self.assertIn(f'"{home if DIRECT else "/home/tester"}/.local/bin/shaffy-desktop"', keybinds)
            for fragment in ('90-omarchy.toml', '94-desktop-controls.toml', '95-desktop-apps.toml'):
                self.assertNotRegex((home / '.config/noctalia' / fragment).read_text(), r'(?<![\w.-])/home/(?!tester(?:/|"))')
            self.assertIn(f'directory = "{home if DIRECT else "/home/tester"}/Screenshots"',
                          (home / '.config/noctalia/90-omarchy.toml').read_text())
            self.assertEqual((home / '.config/git/config').read_text(), GIT_DEFAULTS)
            self.assertFalse((home / '.config/user-dirs.dirs').exists())
            for name in ('Documents', 'Work', '.hidden'):
                self.assertFalse(os.path.lexists(home / name), name)

    def test_plugins_come_from_noctalia_sources_not_this_repository(self):
        """The rendered [plugins] table validates with the sources enabled."""
        import tomllib
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, without=True, keep=keep))
            home = Path(keep) / 'home'
            plugins = tomllib.loads((home / '.config/noctalia/95-desktop-apps.toml').read_text())['plugins']
            self.assertEqual(plugins['auto_update'], 'all')
            self.assertEqual({s['name']: s['enabled'] for s in plugins['source']},
                             {'official': True, 'community': True})
            self.assertEqual(len(plugins['enabled']), 8)
            self.assertNotIn('noctalia/bitwarden', plugins['enabled'])  # until upstream keeps the secret off argv
            self.assertFalse((home / '.local/share/noctalia/plugins').exists())

    def test_broken_configuration_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / 'checkout'
            for part in ('home', 'lib', 'schema', 'defaults'):
                shutil.copytree(ROOT / part, checkout / part,
                                ignore=shutil.ignore_patterns('__pycache__'))
            (checkout / 'scripts').mkdir()
            for script in ('merge-ai-config.py', 'validate-tmux-config.py'):
                shutil.copy(ROOT / 'scripts' / script, checkout / 'scripts')
            with (checkout / 'home/dot_config/niri/cfg/keybinds.kdl.tmpl').open('a') as stream:
                stream.write('\nbinds { Mod+Q { spawn "x" } \n')
            with (checkout / 'home/dot_config/fish/conf.d/40-mise.fish').open('a') as stream:
                stream.write('\nif true\n')
            problems = render(checkout, without=True)['problems']
            self.assertTrue(any(p.startswith('niri: ') for p in problems), problems)
            self.assertTrue(any(p.startswith('fish: .config/fish/conf.d/40-mise.fish') for p in problems),
                            problems)


@unittest.skipIf(REASON or DIRECT, f'bubblewrap sandbox unavailable: {REASON or "direct mode"}')
class SandboxIsolationTests(unittest.TestCase):
    def test_the_sandbox_cannot_write_the_host_or_see_its_processes(self):
        marker = f'shaffy-probe-{os.getpid()}'
        probe = ('for d in /var/tmp /dev/shm /etc /usr "$HOME"; do '
                 f'touch "$d/{marker}" 2>/dev/null && echo "wrote $d"; done; '
                 'echo "processes $(ls /proc | grep -c "^[0-9]")"; id -u; hostname')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sandbox._prepare(root, None)
            result = subprocess.run(sandbox._bwrap(root, ROOT, None, ['/bin/sh', '-c', probe]),
                                    capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.splitlines()
            # Only the synthetic home and the render's own temporary files are writable.
            self.assertEqual([l for l in lines if l.startswith('wrote')],
                             ['wrote /var/tmp', 'wrote /dev/shm', 'wrote /home/tester'])
            self.assertTrue((root / 'home' / marker).exists())
        for host in ('/var/tmp', '/dev/shm'):
            self.assertFalse(os.path.lexists(Path(host) / marker), f'the sandbox wrote the host {host}')
        processes = int(next(l for l in lines if l.startswith('processes')).split()[1])
        self.assertLess(processes, 10, 'the sandbox sees host processes')  # its own pipeline only
        self.assertEqual(lines[-2:], ['4242', 'synthetic-host'])


@unittest.skipUnless(shutil.which('chezmoi'), 'needs chezmoi')
class IgnoreTests(unittest.TestCase):
    """Names without a configured link, and .hidden without links, are ignored, never removed."""
    NAMES = ('Desktop', 'Documents', 'Downloads', 'Music', 'Pictures', 'Projects', 'Public',
             'Templates', 'Videos', 'Work')

    def ignored(self, supplement):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            state = home / '.local/state/shaffy'
            state.mkdir(parents=True)
            binding = {'mode': 'supplement', 'path': str(supplement), 'supplement_id': None} \
                if supplement else {'mode': 'none', 'path': None, 'supplement_id': None}
            (state / 'binding.json').write_text(__import__('json').dumps({'binding': binding}))
            config = Path(directory) / 'chezmoi.toml'
            config.write_text(f'persistentState = "{Path(directory) / "state.boltdb"}"\n')
            result = subprocess.run(
                ['chezmoi', '--config', str(config), '--source', str(ROOT / 'home'), '--destination',
                 str(home), '--no-tty', 'ignored'], capture_output=True, text=True, timeout=120,
                env={'HOME': str(home), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
                     'XDG_RUNTIME_DIR': directory}, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            return set(result.stdout.splitlines())

    def test_without_links_every_name_and_hidden_are_ignored(self):
        ignored = self.ignored(None)
        self.assertLessEqual(set(self.NAMES) | {'.hidden'}, ignored)

    def test_with_links_every_unlisted_name_is_ignored(self):
        ignored = self.ignored(ROOT / 'tests/fixtures/supplements/comprehensive')
        unlisted = set(self.NAMES) - {'Pictures', 'Work'}
        self.assertLessEqual(unlisted, ignored)
        self.assertFalse({'Pictures', 'Work', '.hidden'} & ignored)


class DirectModeTests(unittest.TestCase):
    def test_direct_mode_is_refused_outside_a_container(self):
        with mock.patch.dict(os.environ, {'SHAFFY_RENDER_DIRECT': '1'}), \
                mock.patch.object(sandbox, '_container', return_value=False):
            with self.assertRaisesRegex(RenderError, 'only inside a disposable container'):
                render(ROOT, without=True)

    def test_direct_mode_is_refused_for_a_real_apply(self):
        with mock.patch.dict(os.environ, {'SHAFFY_RENDER_DIRECT': '1'}), \
                mock.patch.object(sandbox, '_container', return_value=True):
            with self.assertRaisesRegex(RenderError, 'preflight of a real apply'):
                render(ROOT, without=True, allow_direct=False)

    def test_direct_mode_is_refused_where_the_account_home_is_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            binding = Path(directory) / '.local/state/shaffy/binding.json'
            binding.parent.mkdir(parents=True)
            binding.write_text('{}')
            entry = mock.Mock(pw_dir=directory)
            with mock.patch.dict(os.environ, {'SHAFFY_RENDER_DIRECT': '1'}), \
                    mock.patch.object(sandbox, '_container', return_value=True), \
                    mock.patch.object(sandbox.pwd, 'getpwuid', return_value=entry):
                with self.assertRaisesRegex(RenderError, 'shares a real home'):
                    render(ROOT, without=True)


@unittest.skipIf(REASON, f'render sandbox unavailable: {REASON}')
class RenderFailureTests(unittest.TestCase):
    def test_a_render_that_times_out_is_a_render_error(self):
        timeout = subprocess.TimeoutExpired(['bwrap'], 1)
        with mock.patch.object(sandbox.subprocess, 'run', side_effect=timeout):
            with self.assertRaisesRegex(RenderError, 'did not finish within 1 seconds'):
                render(ROOT, without=True, timeout=1)


if __name__ == '__main__':
    unittest.main()
