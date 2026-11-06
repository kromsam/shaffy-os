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
from shaffy import sandbox
from shaffy.sandbox import RenderError, render

# The public Git defaults every rendered ~/.config/git/config ends with.
GIT_DEFAULTS = (
    '[init]\n    defaultBranch = main\n'
    '# Defaults: rebase local commits on pull, track the remote branch on the first\n'
    '# push, clearer diffs and remembered conflict resolutions.\n'
    '[pull]\n    rebase = true\n[push]\n    autoSetupRemote = true\n'
    '[diff]\n    algorithm = histogram\n    colorMoved = plain\n    mnemonicPrefix = true\n'
    '[rerere]\n    enabled = true\n[branch]\n    sort = -committerdate\n'
    '[tag]\n    sort = -version:refname\n[column]\n    ui = auto\n'
    '[commit]\n    verbose = true\n'
)
GIT_GITHUB = (
    "# GitHub HTTPS remotes authenticate through the GitHub CLI's login, as\n"
    '# `gh auth setup-git` would write it; the empty helper clears any earlier one.\n'
    '[credential "https://github.com"]\n    helper =\n    helper = !/usr/bin/gh auth git-credential\n'
    '[credential "https://gist.github.com"]\n    helper =\n    helper = !/usr/bin/gh auth git-credential\n'
)

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
    probe = subprocess.run(
        [
            'bwrap',
            '--ro-bind',
            '/',
            '/',
            '--dev',
            '/dev',
            '--proc',
            '/proc',
            '--unshare-all',
            '--new-session',
            '--uid',
            '4242',
            '--',
            'true',
        ],
        capture_output=True,
        check=False,
    )
    return '' if probe.returncode == 0 else 'user namespaces are unavailable for bubblewrap'


REASON = _unavailable()


@unittest.skipIf(REASON, f'render sandbox unavailable: {REASON}')
class RenderTests(unittest.TestCase):
    def assertClean(self, result):
        self.assertEqual(result['problems'], [])
        self.assertTrue(result['converged'])
        self.assertGreaterEqual(result['files'], 70)  # the managed home; plugins come from Noctalia

    def test_public_defaults_render_validate_and_converge(self):
        result = render(ROOT, without=True)
        self.assertClean(result)
        if not DIRECT:
            self.assertEqual(result['identity'], '/home/tester|tester|4242|synthetic-host')

    def test_every_home_gets_the_repositorys_agent_skills(self):
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, without=True, keep=keep))
            home = Path(keep) / 'home'
            for skill in sorted((ROOT / '.agents/skills').iterdir()):
                for host in ('.claude', '.codex'):
                    installed = home / host / 'skills' / skill.name / 'SKILL.md'
                    self.assertEqual(installed.read_text(), (skill / 'SKILL.md').read_text(), installed)
                    self.assertIn('AGENTS.md', (home / host / 'skills/shaffy-os/SKILL.md').read_text())

    def test_minimal_example_renders_like_the_defaults(self):
        self.assertClean(render(ROOT, supplement=ROOT / 'supplement.example'))

    def test_public_defaults_preserve_installer_directories_in_any_language(self):
        import tomllib

        for pictures, documents, downloads, videos in (
            ('Images', 'Documents personnels', 'Téléchargements', "Vidéos d'été"),
            ('画像 & 100%', '書類', 'ダウンロード', '動画'),
        ):
            with self.subTest(pictures=pictures), tempfile.TemporaryDirectory() as keep:
                native = (
                    '# Installer-owned; keep comments and unrelated entries\n'
                    f'XDG_PICTURES_DIR="$HOME/{pictures}"\n'
                    f'XDG_DOCUMENTS_DIR="$HOME/{documents}"\n'
                    f'XDG_DOWNLOAD_DIR="$HOME/{downloads}"\n'
                    f'XDG_VIDEOS_DIR="$HOME/{videos}"\n'
                    'XDG_MUSIC_DIR="$HOME/"\nXDG_TEMPLATES_DIR="/srv/templates"\n'
                )
                self.assertClean(render(ROOT, without=True, keep=keep, user_dirs=native))
                home = Path(keep) / 'home'
                prefix = str(home) if DIRECT else '/home/tester'
                self.assertEqual((home / '.config/user-dirs.dirs').read_text(), native)
                self.assertEqual((home / '.config/user-dirs.dirs').stat().st_mode & 0o777, 0o644)
                shell = tomllib.loads((home / '.config/noctalia/90-shell.toml').read_text())
                apps = tomllib.loads((home / '.config/noctalia/95-desktop-apps.toml').read_text())
                self.assertEqual(shell['shell']['screenshot']['directory'], f'{prefix}/{pictures}/Screenshots')
                self.assertEqual(apps['plugin_settings']['rylos/tailnet']['taildrop_dir'], f'{prefix}/{downloads}')
                self.assertEqual(
                    apps['plugin_settings']['noctalia/screen_recorder']['directory'], f'{prefix}/{videos}/Recordings'
                )
                niri = (home / '.config/niri/cfg/misc.kdl').read_text()
                self.assertIn(pictures.replace('%', '%%') + '/Screenshots/', niri)

    def test_partial_folder_overrides_keep_the_other_installer_entries(self):
        native = (
            '# Installer choice\nXDG_PICTURES_DIR="$HOME/Images"\n'
            'XDG_DOCUMENTS_DIR="$HOME/Documents personnels"\n'
            'XDG_DOWNLOAD_DIR="$HOME/Téléchargements"\nXDG_MUSIC_DIR="$HOME/"\n'
        )
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(
                render(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive', keep=keep, user_dirs=native)
            )
            home = Path(keep) / 'home'
            expected = native.replace('$HOME/Images', '$HOME/Bilder') + 'XDG_VIDEOS_DIR="$HOME/Filme"\n'
            self.assertEqual((home / '.config/user-dirs.dirs').read_text(), expected)

    def test_niri_screenshot_path_escapes_native_quotes_and_strftime_percent(self):
        native = 'XDG_PICTURES_DIR="$HOME/Photos \\"été\\" & 100%"\n'
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, without=True, keep=keep, user_dirs=native))
            niri = (Path(keep) / 'home/.config/niri/cfg/misc.kdl').read_text()
            self.assertIn('Photos \\"été\\" & 100%%/Screenshots/', niri)

    def test_supplement_settings_reach_the_rendered_files(self):
        with tempfile.TemporaryDirectory() as keep:
            result = render(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive', keep=keep)
            self.assertClean(result)
            home = Path(keep) / 'home'
            codex = (home / '.codex/config.toml').read_text()
            self.assertIn('[mcp_servers.notes]', codex)
            self.assertIn('ZOTERO_LOCAL_WRITE = "true"', codex)
            self.assertIn(f'command = "{home if DIRECT else "/home/tester"}/.local/bin/example-notes-mcp"', codex)
            display = (home / '.config/niri/cfg/display.kdl').read_text()
            self.assertIn(
                'output "DP-9" {\n    mode "2560x1440@59.951"\n    scale 1.25\n    position x=0 y=0\n}', display
            )
            self.assertIn('output "HDMI-A-7" {\n}', display)
            keyboard = (home / '.config/niri/cfg/input.kdl').read_text()
            self.assertIn(
                'layout "us,de"\n            variant "intl,"\n            options "grp:alt_shift_toggle"', keyboard
            )
            extras = json.loads((home / '.config/nvim/lazyvim.json').read_text())['extras']
            self.assertEqual(extras[-2:], ['lazyvim.plugins.extras.lang.tailwind', 'lazyvim.plugins.extras.lang.ruby'])
            claude = json.loads((home / '.claude/settings.json').read_text())
            self.assertIs(claude['enabledPlugins']['example-lsp@example-plugins'], True)
            self.assertEqual(claude['extraKnownMarketplaces']['example-plugins']['source']['repo'], 'example/plugins')

    def test_folder_and_wallpaper_settings_reach_noctalia(self):
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive', keep=keep))
            home = Path(keep) / 'home'
            prefix = str(home) if DIRECT else '/home/tester'
            shell = (home / '.config/noctalia/90-shell.toml').read_text()
            self.assertIn(f'directory = "{prefix}/Bilder/Screenshots"', shell)
            self.assertIn(f'directory = "{prefix}/Wallpapers"', shell)
            self.assertIn('path = "/usr/share/backgrounds/example.png"', shell)
            apps = (home / '.config/noctalia/95-desktop-apps.toml').read_text()
            self.assertIn(f'directory = "{prefix}/Filme/Recordings"', apps)

    def test_password_managers_and_credential_prompts_are_blocked_from_capture(self):
        import re

        rules = (ROOT / 'home/dot_config/niri/cfg/rules.kdl').read_text()
        rule = re.search(r'match app-id=r#"([^"]+)"#\s*block-out-from "screen-capture"', rules)
        self.assertIsNotNone(rule)
        pattern = re.compile(rule[1])
        for app_id in (
            'Proton Pass',
            'Bitwarden',
            '1Password',
            'org.keepassxc.KeePassXC',
            'gcr-prompter',
            'pinentry-qt',
            'org.gnupg.pinentry-qt',
            'org.gnome.World.Secrets',
        ):
            self.assertRegex(app_id, pattern)
        for app_id in ('Alacritty', 'zen', 'SilverBullet', 'org.gnome.Nautilus'):
            self.assertNotRegex(app_id, pattern)

    def test_proton_pass_desktop_app_is_a_default_application(self):
        import yaml

        group = yaml.safe_load((ROOT / 'defaults/packages/desktop_apps.yml').read_text())
        self.assertIn('proton-pass', group['packages'])
        self.assertNotIn('proton-pass', group.get('required', []), 'a supplement may omit it')

    def test_noctalia_public_defaults_skip_the_wizard_and_lock_when_idle(self):
        import tomllib

        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, without=True, keep=keep))
            home = Path(keep) / 'home'
            shell = tomllib.loads((home / '.config/noctalia/90-shell.toml').read_text())
            keybinds = (home / '.config/niri/cfg/keybinds.kdl').read_text()
        self.assertIs(shell['shell']['setup_wizard_enabled'], False)
        self.assertIs(shell['shell']['polkit_agent'], True)
        behaviors = shell['idle']['behavior']
        self.assertEqual((behaviors['lock']['action'], behaviors['lock']['timeout']), ('lock', 300))
        self.assertEqual(behaviors['screen-off']['action'], 'screen_off')
        self.assertGreater(behaviors['screen-off']['timeout'], behaviors['lock']['timeout'])
        self.assertTrue(all(behavior['enabled'] for behavior in behaviors.values()))
        self.assertNotIn('suspend', behaviors)  # a laptop that suspends by itself loses work
        self.assertIn('Mod+Ctrl+I', keybinds)
        self.assertIn('"caffeine-toggle"', keybinds)

    def test_overrides_are_rendered_last_and_win(self):
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive', keep=keep))
            home = Path(keep) / 'home'
            self.assertIn('Mod+F12', (home / '.config/niri/user.kdl').read_text())
            self.assertTrue((home / '.config/niri/config.kdl').read_text().rstrip().endswith('include "user.kdl"'))
            self.assertIn('top_center', (home / '.config/noctalia/97-supplement.toml').read_text())
            imports = __import__('tomllib').loads((home / '.config/alacritty/alacritty.toml').read_text())['general'][
                'import'
            ]
            self.assertEqual(
                imports,
                [
                    '~/.config/alacritty/themes/noctalia.toml',
                    '~/.config/alacritty/shaffy.toml',
                    '~/.config/alacritty/supplement.toml',
                ],
            )
            self.assertIn('size = 11.0', (home / '.config/alacritty/supplement.toml').read_text())
            # fish reads conf.d first and config.fish last; the supplement must win over both.
            greeting = subprocess.run(
                ['fish', '-c', 'fish_greeting'],
                capture_output=True,
                text=True,
                timeout=30,
                env={
                    'HOME': str(home),
                    'XDG_CONFIG_HOME': str(home / '.config'),
                    'PATH': '/usr/bin:/bin',
                    'LANG': 'C.UTF-8',
                },
                check=False,
            )
            self.assertIn('synthetic-supplement-greeting', greeting.stdout)

    def test_without_overrides_the_override_files_exist_and_are_empty(self):
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, without=True, keep=keep))
            home = Path(keep) / 'home'
            for target in (
                '.config/niri/user.kdl',
                '.config/noctalia/97-supplement.toml',
                '.config/fish/supplement.fish',
                '.config/alacritty/supplement.toml',
            ):
                self.assertEqual((home / target).read_text(), '', target)
            # The public wallpaper is the one the noctalia package installs; none is redistributed.
            shell = (home / '.config/noctalia/90-shell.toml').read_text()
            self.assertEqual(shell.count('"/usr/share/noctalia/assets/noctalia-wallpaper.png"'), 3)
            self.assertFalse((home / '.local/share/wallpapers').exists())

    def test_identity_and_folders_render_from_the_settings(self):
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive', keep=keep))
            home = Path(keep) / 'home'
            self.assertEqual(
                (home / '.config/git/config').read_text(),
                '[user]\n    name = Test Person\n    email = test@example.invalid\n'
                '    signingkey = /etc/os-release\n'
                + GIT_DEFAULTS
                + '    gpgsign = true\n[gpg]\n    format = ssh\n'
                + GIT_GITHUB,
            )
            dirs = (home / '.config/user-dirs.dirs').read_text()
            self.assertIn('XDG_PICTURES_DIR="$HOME/Bilder"', dirs)
            self.assertNotIn('XDG_DOCUMENTS_DIR=', dirs)

    def test_assets_are_copied_from_the_supplement(self):
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive', keep=keep))
            home = Path(keep) / 'home'
            self.assertEqual(
                (home / '.local/share/fonts/Example-Regular.otf').read_text(), 'OTTO synthetic font bytes\n'
            )
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
            config.write_text(
                config.read_text().replace(
                    '  "~/Wallpapers/example photo.jpg"', '  "~/.config/Zettlr/example photo.jpg"'
                )
            )  # zettlr is disabled
            problems = render(ROOT, supplement=supplement)['problems']
            self.assertIn('assets.~/.config/Zettlr/example photo.jpg: its target is ignored here', '\n'.join(problems))

    def test_account_endpoints_render_only_when_set(self):
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive', keep=keep))
            home = Path(keep) / 'home'
            self.assertIn(
                'admin_url = "https://headscale.example.invalid"',
                (home / '.config/noctalia/95-desktop-apps.toml').read_text(),
            )
            self.assertIn(
                f'screenshot-path "{home if DIRECT else "/home/tester"}/Bilder/Screenshots/',
                (home / '.config/niri/cfg/misc.kdl').read_text(),
            )
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, without=True, keep=keep))
            home = Path(keep) / 'home'
            self.assertNotIn('admin_url', (home / '.config/noctalia/95-desktop-apps.toml').read_text())
            launcher = (home / '.local/share/applications/silverbullet.desktop').read_text()
            appimage = f'{home if DIRECT else "/home/tester"}/.local/lib/silverbullet-desktop/SilverBullet.AppImage'
            self.assertIn(f'Exec="{appimage}" %U', launcher)
            self.assertIn(f'TryExec={appimage}', launcher)
            self.assertNotIn('--no-sandbox', launcher)
            self.assertNotIn('text/markdown', launcher)  # Markdown files keep their own application
            beeper = (home / '.local/share/applications/Beeper.desktop').read_text()
            appimage = f'{home if DIRECT else "/home/tester"}/.local/lib/beeper/Beeper.AppImage'
            self.assertIn(f'Exec="{appimage}" %U', beeper)
            self.assertIn(f'TryExec={appimage}', beeper)
            self.assertNotIn('--no-sandbox', beeper)
            self.assertIn(
                f'screenshot-path "{home if DIRECT else "/home/tester"}/Screenshots/',
                (home / '.config/niri/cfg/misc.kdl').read_text(),
            )
            # Export profiles, citation styles and Pandoc filters are a supplement's house style.
            self.assertFalse((home / '.local/share/pandoc').exists())
            # Docker belongs to the default desktop, so its rootless engine needs no supplement.
            self.assertEqual(
                os.readlink(home / '.config/systemd/user/default.target.wants/docker.service'),
                '/usr/lib/systemd/user/docker.service',
            )
            self.assertIn(
                'DOCKER_HOST=unix://${XDG_RUNTIME_DIR}/docker.sock',
                (home / '.config/environment.d/40-docker.conf').read_text(),
            )
            self.assertIn('DOCKER_HOST', (home / '.config/fish/conf.d/25-docker.fish').read_text())
            self.assertEqual(json.loads((home / '.config/docker/daemon.json').read_text())['ip'], '127.0.0.1')

    def test_integrations_render_only_while_managed(self):
        wants = '.config/systemd/user/default.target.wants/syncthing.service'
        ssh_autostart = '.config/autostart/shaffy-proton-pass.desktop'
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive', keep=keep))
            home = Path(keep) / 'home'
            self.assertEqual(os.readlink(home / wants), '/usr/lib/systemd/user/syncthing.service')
            self.assertIn(
                'TryExec=/usr/bin/proton-pass\nExec=/usr/bin/proton-pass\n', (home / ssh_autostart).read_text()
            )
            self.assertTrue((home / '.config/autostart/Nextcloud.desktop').is_file())
            self.assertTrue((home / '.ssh/config').is_file())
            # The agent environment follows the managed desktop integration.
            self.assertEqual(
                (home / '.config/environment.d/90-proton-pass-ssh-agent.conf').read_text(),
                'SSH_AUTH_SOCK=${HOME}/.ssh/proton-pass-ssh-agent.sock\n',
            )
            self.assertIn('SSH_AUTH_SOCK', (home / '.config/fish/conf.d/30-ssh-agent.fish').read_text())
            fish = home / '.config/fish/conf.d/30-ssh-agent.fish'
            for forwarded in (None, '/synthetic/forwarded-agent.sock'):
                env = {**os.environ, 'HOME': str(home)}
                env.pop('SSH_AUTH_SOCK', None)
                if forwarded:
                    env['SSH_AUTH_SOCK'] = forwarded
                result = subprocess.run(
                    ['fish', '--no-config', '-c', 'source "$argv[1]"; printf %s "$SSH_AUTH_SOCK"', str(fish)],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, forwarded or str(home / '.ssh/proton-pass-ssh-agent.sock'))
            rendered_home = str(home) if DIRECT else '/home/tester'
            self.assertIn(
                f'SSH_AUTH_SOCK {json.dumps(rendered_home + "/.ssh/proton-pass-ssh-agent.sock")}',
                (home / '.config/niri/cfg/misc.kdl').read_text(),
            )
            self.assertIn('Path=shaffy.default\n', (home / '.zotero/zotero/profiles.ini').read_text())
            prefs = (home / '.zotero/zotero/shaffy.default/user.js').read_text()
            self.assertIn(
                f'"extensions.zotero.dataDir", "{home if DIRECT else "/home/tester"}/.local/share/zotero-data"', prefs
            )
            # The data directory user.js names exists before Zotero first starts.
            self.assertTrue((home / '.local/share/zotero-data').is_dir())
            # The folder Better BibTeX's export goes to, which Zettlr cites from.
            self.assertTrue((home / '.local/share/zotero-library').is_dir())
            # A fresh home has no REAPER or Zettlr configuration for their modifiers to change,
            # and the fixture disables zettlr, so none of its files render.
            self.assertFalse((home / '.config/REAPER/reaper.ini').exists())
            self.assertFalse((home / '.config/Zettlr/defaults').exists())
            # Backup settings configure the client; its timers wait for the integration.
            backup = json.loads((home / '.config/restic-home/backup.json').read_text())
            self.assertEqual(
                backup,
                {
                    'home': str(home) if DIRECT else '/home/tester',
                    'host': 'synthetic-laptop',
                    'tags': ['automatic', 'shaffy'],
                    'repository': 'sftp:restic-home-nas:repository',
                    'repository_id': '0123456789abcdef' * 4,
                    'retention': {'daily': 7, 'weekly': 8, 'monthly': 12},
                },
            )
            self.assertIn(
                '    HostName backup.example.invalid\n    Port 2222\n    User restic-test\n',
                (home / '.config/restic-home/ssh_config').read_text(),
            )
            self.assertTrue(
                (home / '.config/restic-home/known_hosts')
                .read_text()
                .startswith('[backup.example.invalid]:2222 ssh-ed25519 AAAA')
            )
            self.assertTrue(
                (home / '.config/restic-home/excludes')
                .read_text()
                .endswith('# From the settings.\n$HOME/Projects/scratch data\n')
            )
            # restic cannot read Docker's sub-UID files, and restic-home fails on what it cannot read.
            self.assertIn('$HOME/.local/share/docker\n', (home / '.config/restic-home/excludes').read_text())
            self.assertTrue((home / '.config/systemd/user/restic-home-backup.timer').is_file())
            self.assertFalse(
                os.path.lexists(home / '.config/systemd/user/timers.target.wants/restic-home-backup.timer')
            )
            # The services may write only there, so it must exist before a timer first runs them.
            receipts = home / '.local/state/shaffy/home-backup'
            self.assertEqual(
                (receipts.is_dir(), receipts.stat().st_mode & 0o777, list(receipts.iterdir())), (True, 0o700, [])
            )
            self.assertIn(
                ' %h/.local/state/shaffy/home-backup',
                (home / '.config/systemd/user/restic-home-backup.service').read_text(),
            )
        with tempfile.TemporaryDirectory() as keep:
            self.assertClean(render(ROOT, without=True, keep=keep))
            home = Path(keep) / 'home'
            for target in (
                wants,
                ssh_autostart,
                '.config/autostart/Nextcloud.desktop',
                '.ssh/config',
                '.config/Zettlr',
                '.zotero',
                '.local/share/zotero-data',
                '.local/share/zotero-library',
                '.config/restic-home',
                '.local/bin/restic-home',
                '.local/state/shaffy/home-backup',
                '.config/systemd/user/restic-home-backup.service',
                '.config/environment.d/90-proton-pass-ssh-agent.conf',
                '.config/fish/conf.d/30-ssh-agent.fish',
            ):
                self.assertFalse(os.path.lexists(home / target), target)
            self.assertNotIn('SSH_AUTH_SOCK', (home / '.config/niri/cfg/misc.kdl').read_text())

    def test_disabled_and_released_ssh_render_no_agent_environment_or_activation(self):
        for state in ('disabled', 'released'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
                supplement = Path(temporary) / 'supplement'
                shutil.copytree(ROOT / 'tests/fixtures/supplements/comprehensive', supplement)
                config = supplement / 'config.yaml'
                config.write_text(config.read_text().replace('ssh: managed', f'ssh: {state}'))
                keep = Path(temporary) / 'render'
                keep.mkdir()
                self.assertClean(render(ROOT, supplement=supplement, keep=keep))
                home = keep / 'home'
                for target in (
                    '.config/autostart/shaffy-proton-pass.desktop',
                    '.config/environment.d/90-proton-pass-ssh-agent.conf',
                    '.config/fish/conf.d/30-ssh-agent.fish',
                    '.ssh/config',
                ):
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
            for fragment in ('90-shell.toml', '94-desktop-controls.toml', '95-desktop-apps.toml'):
                self.assertNotRegex(
                    (home / '.config/noctalia' / fragment).read_text(), r'(?<![\w.-])/home/(?!tester(?:/|"))'
                )
            self.assertIn(
                f'directory = "{home if DIRECT else "/home/tester"}/Screenshots"',
                (home / '.config/noctalia/90-shell.toml').read_text(),
            )
            self.assertEqual((home / '.config/git/config').read_text(), GIT_DEFAULTS + GIT_GITHUB)
            self.assertFalse((home / '.config/user-dirs.dirs').exists())

    def test_plugins_come_from_noctalia_sources_not_this_repository(self):
        """Noctalia resolves the rendered fragments to its official and community sources, enabled
        and updating every plugin."""
        import tomllib

        with tempfile.TemporaryDirectory() as keep, tempfile.TemporaryDirectory() as scratch:
            self.assertClean(render(ROOT, without=True, keep=keep))
            home = Path(keep) / 'home'
            env = {'PATH': os.environ['PATH'], 'HOME': scratch, 'XDG_CONFIG_HOME': str(home / '.config')}
            for kind in ('STATE', 'DATA', 'CACHE'):
                env[f'XDG_{kind}_HOME'] = scratch
            exported = subprocess.run(
                ['noctalia', 'config', 'export', 'full'], env=env, capture_output=True, text=True, check=True
            )
            plugins = tomllib.loads(exported.stdout)['plugins']
            self.assertEqual(plugins['auto_update'], 'all')
            self.assertEqual(
                {s['name']: s['enabled'] for s in plugins['source']}, {'official': True, 'community': True}
            )
            self.assertEqual(len(plugins['enabled']), 7)
            self.assertFalse((home / '.local/share/noctalia/plugins').exists())

    def test_broken_configuration_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / 'checkout'
            for part in ('home', '.agents', 'lib', 'schema', 'defaults'):
                shutil.copytree(ROOT / part, checkout / part, ignore=shutil.ignore_patterns('__pycache__'))
            (checkout / 'scripts').mkdir()
            for script in ('merge-ai-config.py', 'validate-tmux-config.py'):
                shutil.copy(ROOT / 'scripts' / script, checkout / 'scripts')
            with (checkout / 'home/dot_config/niri/cfg/keybinds.kdl.tmpl').open('a') as stream:
                stream.write('\nbinds { Mod+Q { spawn "x" } \n')
            with (checkout / 'home/dot_config/fish/conf.d/40-mise.fish').open('a') as stream:
                stream.write('\nif true\n')
            problems = render(checkout, without=True)['problems']
            self.assertTrue(any(p.startswith('niri: ') for p in problems), problems)
            self.assertTrue(any(p.startswith('fish: .config/fish/conf.d/40-mise.fish') for p in problems), problems)


@unittest.skipIf(REASON or DIRECT, f'bubblewrap sandbox unavailable: {REASON or "direct mode"}')
class SandboxIsolationTests(unittest.TestCase):
    def test_the_sandbox_cannot_write_the_host_or_see_its_processes(self):
        marker = f'shaffy-probe-{os.getpid()}'
        probe = (
            'for d in /var/tmp /dev/shm /etc /usr "$HOME"; do '
            f'touch "$d/{marker}" 2>/dev/null && echo "wrote $d"; done; '
            'echo "processes $(ls /proc | grep -c "^[0-9]")"; id -u; hostname'
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sandbox._prepare(root, None)
            result = subprocess.run(
                sandbox._bwrap(root, ROOT, None, ['/bin/sh', '-c', probe]),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.splitlines()
            # Only the synthetic home and the render's own temporary files are writable.
            self.assertEqual(
                [line for line in lines if line.startswith('wrote')],
                ['wrote /var/tmp', 'wrote /dev/shm', 'wrote /home/tester'],
            )
            self.assertTrue((root / 'home' / marker).exists())
        for host in ('/var/tmp', '/dev/shm'):
            self.assertFalse(os.path.lexists(Path(host) / marker), f'the sandbox wrote the host {host}')
        processes = int(next(line for line in lines if line.startswith('processes')).split()[1])
        self.assertLess(processes, 10, 'the sandbox sees host processes')  # its own pipeline only
        self.assertEqual(lines[-2:], ['4242', 'synthetic-host'])


class DirectModeTests(unittest.TestCase):
    def test_direct_mode_is_refused_outside_a_container(self):
        with (
            mock.patch.dict(os.environ, {'SHAFFY_RENDER_DIRECT': '1'}),
            mock.patch.object(sandbox, '_container', return_value=False),
        ):
            with self.assertRaisesRegex(RenderError, 'only inside a disposable container'):
                render(ROOT, without=True)

    def test_direct_mode_is_refused_for_a_real_apply(self):
        with (
            mock.patch.dict(os.environ, {'SHAFFY_RENDER_DIRECT': '1'}),
            mock.patch.object(sandbox, '_container', return_value=True),
        ):
            with self.assertRaisesRegex(RenderError, 'preflight of a real apply'):
                render(ROOT, without=True, allow_direct=False)

    def test_direct_mode_is_refused_where_the_account_home_is_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            binding = Path(directory) / '.local/state/shaffy/binding.json'
            binding.parent.mkdir(parents=True)
            binding.write_text('{}')
            entry = mock.Mock(pw_dir=directory)
            with (
                mock.patch.dict(os.environ, {'SHAFFY_RENDER_DIRECT': '1'}),
                mock.patch.object(sandbox, '_container', return_value=True),
                mock.patch.object(sandbox.pwd, 'getpwuid', return_value=entry),
            ):
                with self.assertRaisesRegex(RenderError, 'shares a real home'):
                    render(ROOT, without=True)


@unittest.skipIf(REASON, f'render sandbox unavailable: {REASON}')
class RenderFailureTests(unittest.TestCase):
    def test_a_render_that_times_out_is_a_render_error(self):
        timeout = subprocess.TimeoutExpired(['bwrap'], 1)
        # The release lookup runs Git through the same subprocess.run; only the render times out.
        with (
            mock.patch.object(sandbox, 'release', return_value=None),
            mock.patch.object(sandbox.subprocess, 'run', side_effect=timeout),
        ):
            with self.assertRaisesRegex(RenderError, 'did not finish within 1 seconds'):
                render(ROOT, without=True, timeout=1)


if __name__ == '__main__':
    unittest.main()
