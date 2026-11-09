"""The boot splash: the shaffy-os theme and its wordmark, the desktop.boot_splash setting, and the script that
installs and selects a theme, against a fake CachyOS whose Plymouth and limine-mkinitcpio act on a temporary
root; and the role that runs it."""

import configparser
from contextlib import redirect_stderr
import io
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(ROOT / 'tests'))
from shaffy import bootstrap
from shaffy.loader import SettingsError, load
from support import host_test

SCRIPT = runpy.run_path(str(ROOT / 'scripts/configure-boot-splash.py'))
WORDMARK = runpy.run_path(str(ROOT / 'scripts/build-splash-wordmark.py'))
THEME = ROOT / 'roles/boot_splash/files/shaffy-os'
THEMES = 'usr/share/plymouth/themes'
# What two-step and Plymouth's graphics library load from a theme's ImageDir, besides the optional
# images CachyOS's theme does not have: the password prompt, the indicators, the logo and the spinner.
NEEDED = ('lock.png', 'entry.png', 'bullet.png', 'capslock.png', 'keyboard.png', 'keymap-render.png', 'watermark.png')
FRAMES = ('throbber-', 'animation-')
# plymouthd.conf as CachyOS's graphical installer leaves it: plymouth-set-default-theme put the theme at
# the top of the package's file, and the installer switched on simpledrm in both [Daemon] lines.
INSTALLED_CONF = (
    '[Daemon]\nUseSimpledrm=1\nTheme=cachyos-bootanimation\n# Administrator customizations go in this file\n'
    '[Daemon]\nUseSimpledrm=1\n#Theme=fade-in\n'
)
KERNELS = ('linux-cachyos', 'linux-cachyos-lts')


def png_size(data):
    return struct.unpack('>II', data[16:24])


class FakeMachine:
    """A fresh CachyOS in `root`; called as the script's runner, Plymouth and limine-mkinitcpio act on it."""

    def __init__(self, root):
        self.root, self.calls, self.images = root, [], {}
        self.fail = self.skip = False
        self.reads = None  # what plymouth-set-default-theme reads instead, when set
        files = {
            'etc/plymouth/plymouthd.conf': INSTALLED_CONF,
            'usr/share/plymouth/plymouthd.defaults': '[Daemon]\nTheme=bgrt\n',
            'proc/cmdline': 'quiet nowatchdog splash rw rootflags=subvol=/@ root=UUID=0000\n',
            'usr/bin/plymouth-set-default-theme': '',
            'usr/bin/limine-mkinitcpio': '',
            'usr/lib/plymouth/two-step.so': '',
            f'{THEMES}/cachyos/cachyos.plymouth': '[Plymouth Theme]\nName=CachyOS\nModuleName=two-step\n',
            **{
                f'{THEMES}/{name}/{name}.plymouth': f'[Plymouth Theme]\nName={name}\nModuleName=two-step\n'
                for name in ('cachyos-bootanimation', 'spinner', 'custom')
            },
        }
        names = [*NEEDED, *(f'{prefix}{n:04}.png' for prefix in FRAMES for n in (1, 2, 3))]
        files |= {f'{THEMES}/cachyos/{name}': f'cachyos {name}' for name in names}
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)

    def selected(self):
        """plymouth-set-default-theme without arguments: the [Daemon] Theme, if its theme file is readable,
        else the distribution's default."""
        config = configparser.ConfigParser(strict=False, interpolation=None)
        config.read_string((self.root / 'etc/plymouth/plymouthd.conf').read_text())
        theme = config.get('Daemon', 'Theme', fallback=None)
        if theme and (self.root / THEMES / theme / f'{theme}.plymouth').is_file():
            return theme
        return 'bgrt'

    def __call__(self, *command):
        self.calls.append(command)
        if command == ('plymouth-set-default-theme',):
            return (self.reads or self.selected()) + '\n'
        if command == ('limine-mkinitcpio',):
            if self.fail:
                raise RuntimeError('limine-mkinitcpio failed with status 3:\nERROR: post hook failed')
            if self.skip:
                return f'Building initramfs for {KERNELS[0]}\nERROR: mkinitcpio failed for kernel 6.18.1-1, skipping.\n'
            # mkinitcpio's plymouth hook: the selected theme's folder, as add_full_dir adds it, links as links.
            theme = self.selected()
            folder = self.root / THEMES / theme
            self.images = {
                kernel: {
                    p.name: ('link', str(p.readlink())) if p.is_symlink() else p.read_bytes() for p in folder.iterdir()
                }
                | {'theme': theme}
                for kernel in KERNELS
            }
            return 'Building initramfs for linux-cachyos\n'
        raise AssertionError(f'unexpected command {command}')

    def rebuilds(self):
        return self.calls.count(('limine-mkinitcpio',))


def tree(root):
    """Every path under root with its content, to show that nothing was written."""
    return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else None for p in sorted(root.rglob('*'))}


class ThemeTests(unittest.TestCase):
    def test_the_theme_file_names_its_role_two_step_and_its_own_folder(self):
        text = (THEME / 'shaffy-os.plymouth').read_text()
        self.assertTrue(text.startswith('# Managed by Shaffy OS (the boot_splash role)'))
        theme = configparser.ConfigParser(interpolation=None)
        theme.read_string(text)
        self.assertEqual(theme['Plymouth Theme']['Name'], 'Shaffy OS')
        self.assertEqual(theme['Plymouth Theme']['ModuleName'], 'two-step')
        self.assertEqual(theme['two-step']['ImageDir'], '/usr/share/plymouth/themes/shaffy-os')
        self.assertEqual(sorted(p.name for p in THEME.iterdir()), ['shaffy-os.plymouth', 'watermark.png'])

    @host_test(
        'compares with the installed CachyOS theme', paths=('/usr/share/plymouth/themes/cachyos/cachyos.plymouth',)
    )
    def test_only_the_name_description_and_folder_differ_from_cachyos(self):
        ours = [line for line in (THEME / 'shaffy-os.plymouth').read_text().splitlines() if not line.startswith('#')]
        theirs = Path('/usr/share/plymouth/themes/cachyos/cachyos.plymouth').read_text().splitlines()
        changed = {line.split('=', 1)[0] for line in set(ours) ^ set(theirs)}
        self.assertEqual(len(ours), len(theirs))
        self.assertEqual(changed, {'Name', 'Description', 'ImageDir'})

    def test_the_committed_wordmark_has_the_size_the_script_draws(self):
        data = (THEME / 'watermark.png').read_bytes()
        self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(png_size(data), (WORDMARK['WIDTH'], WORDMARK['HEIGHT']))

    @host_test('draws with librsvg and the desktop font', tools=('rsvg-convert', 'fc-match'))
    def test_the_wordmark_script_draws_a_png_of_its_size(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'watermark.png'
            try:
                WORDMARK['build'](output)
            except RuntimeError as error:  # the font is missing
                self.skipTest(str(error))
            self.assertEqual(png_size(output.read_bytes()), (WORDMARK['WIDTH'], WORDMARK['HEIGHT']))


class SettingTests(unittest.TestCase):
    def test_the_default_is_shaffy_os_and_a_supplement_names_another_or_false(self):
        self.assertEqual(load(ROOT, without=True)['shaffy']['settings']['desktop']['boot_splash'], 'shaffy-os')
        fixture = load(ROOT, supplement=ROOT / 'tests/fixtures/supplements/comprehensive')
        self.assertEqual(fixture['shaffy']['settings']['desktop']['boot_splash'], 'spinner')
        for value, valid in (
            (False, True),
            ('cachyos-bootanimation', True),
            (True, False),
            (0, False),
            ('', False),
            ('../cachyos', False),
            ('themes/cachyos', False),
            ('x' * 65, False),
        ):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                config = {'schema_version': 1, 'supplement_id': 'splash-test', 'desktop': {'boot_splash': value}}
                Path(directory, 'config.yaml').write_text(yaml.safe_dump(config))
                if valid:
                    self.assertEqual(
                        load(ROOT, supplement=directory)['shaffy']['settings']['desktop']['boot_splash'], value
                    )
                else:
                    with self.assertRaises(SettingsError):
                        load(ROOT, supplement=directory)

    def test_the_script_takes_only_a_theme_name(self):
        for value in ('../etc', 'a/b', '', '-x'):
            with self.subTest(value=value), self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                SCRIPT['main'](['--theme', value])


class SelectionTests(unittest.TestCase):
    def test_the_selection_is_the_first_theme_of_a_daemon_section(self):
        self.assertEqual(SCRIPT['selection'](INSTALLED_CONF), 'cachyos-bootanimation')
        self.assertIsNone(
            SCRIPT['selection']('# Administrator customizations go in this file\n#[Daemon]\n#Theme=fade-in\n')
        )
        self.assertIsNone(SCRIPT['selection']('[Other]\nTheme=spinner\n'))

    def test_selecting_changes_the_theme_line_only(self):
        selecting = SCRIPT['selecting']
        self.assertEqual(
            selecting(INSTALLED_CONF, 'shaffy-os'), INSTALLED_CONF.replace('cachyos-bootanimation', 'shaffy-os')
        )
        package = '# Administrator customizations go in this file\n#[Daemon]\n#Theme=fade-in\n'
        self.assertEqual(selecting(package, 'shaffy-os'), '[Daemon]\nTheme=shaffy-os\n' + package)
        self.assertEqual(selecting('[Daemon]\nShowDelay=0\n', 'spinner'), '[Daemon]\nTheme=spinner\nShowDelay=0\n')
        self.assertEqual(selecting('', 'spinner'), '[Daemon]\nTheme=spinner\n')


class ConvergeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root, self.state = Path(directory.name) / 'root', Path(directory.name) / 'state'
        self.root.mkdir()
        self.machine = FakeMachine(self.root)

    def converge(self, theme, apply=True):
        splash = SCRIPT['Splash'](self.root, self.state, apply, self.machine, THEME)
        changed = splash.converge(theme)
        return changed, splash.notes

    def conf(self):
        return (self.root / 'etc/plymouth/plymouthd.conf').read_text()

    def installed(self):
        return {p.name: p for p in (self.root / THEMES / 'shaffy-os').iterdir()}

    def record(self):
        return yaml.safe_load((self.state / 'boot-splash.json').read_text())

    def test_a_fresh_installation_gets_the_shaffy_os_splash_with_one_rebuild(self):
        self.assertEqual(self.converge('shaffy-os'), (True, []))
        self.assertEqual(self.conf(), INSTALLED_CONF.replace('cachyos-bootanimation', 'shaffy-os'))
        self.assertEqual(self.machine.rebuilds(), 1)
        self.assertEqual(self.record(), {'selected': 'shaffy-os', 'rebuild': False})
        copies = list(self.state.glob('boot-splash-copies/*/etc/plymouth/plymouthd.conf'))
        self.assertEqual([c.read_text() for c in copies], [INSTALLED_CONF])
        installed = self.installed()
        self.assertEqual(installed['watermark.png'].read_bytes(), (THEME / 'watermark.png').read_bytes())
        self.assertEqual(installed['lock.png'].read_bytes(), b'cachyos lock.png')
        self.assertNotIn('cachyos.plymouth', installed)

    def test_every_image_two_step_needs_is_a_file_in_each_boot_image(self):
        self.converge('shaffy-os')
        for kernel, image in self.machine.images.items():
            with self.subTest(kernel=kernel):
                self.assertEqual(image['theme'], 'shaffy-os')
                for name in NEEDED:
                    self.assertIsInstance(image[name], bytes, name)  # not a link, which would dangle there
                for prefix in FRAMES:
                    self.assertTrue(any(n.startswith(prefix) and isinstance(d, bytes) for n, d in image.items()))

    def test_a_second_apply_changes_and_runs_nothing(self):
        self.converge('shaffy-os')
        before, self.machine.calls = tree(self.root), []
        self.assertEqual(self.converge('shaffy-os'), (False, []))
        self.assertEqual(self.machine.calls, [])
        self.assertEqual(tree(self.root), before)

    def test_a_check_reports_without_writing(self):
        before = tree(self.root)
        changed, notes = self.converge('shaffy-os', apply=False)
        self.assertTrue(changed)
        self.assertIn('would select shaffy-os in /etc/plymouth/plymouthd.conf', notes)
        self.assertEqual((tree(self.root), self.machine.calls), (before, []))
        self.assertFalse(self.state.exists())

    def test_another_selection_is_rebuilt_once(self):
        self.converge('shaffy-os')
        self.machine.calls = []
        self.assertEqual(self.converge('spinner'), (True, []))
        self.assertIn('Theme=spinner\n', self.conf())
        self.assertEqual(self.machine.rebuilds(), 1)
        self.assertEqual(self.machine.images[KERNELS[0]]['theme'], 'spinner')
        self.assertEqual(self.converge('spinner'), (False, []))

    def test_a_missing_theme_is_refused_before_anything_is_written(self):
        before = tree(self.root)
        with self.assertRaisesRegex(RuntimeError, 'desktop.boot_splash names example-theme'):
            self.converge('example-theme')
        self.assertEqual((tree(self.root), self.machine.calls), (before, []))
        self.assertFalse(self.state.exists())

    def test_missing_plymouth_limine_or_splash_is_refused_before_anything_is_written(self):
        for missing, message in (
            ('usr/bin/plymouth-set-default-theme', 'plymouth-set-default-theme is missing'),
            ('usr/bin/limine-mkinitcpio', 'limine-mkinitcpio is missing'),
            ('splash', 'has no splash'),
            ('enrolment', 'no hook in /etc/boot/hooks/post.d/ runs limine-enroll-config'),
        ):
            with self.subTest(missing=missing):
                machine_root = self.root.with_name(missing.replace('/', '-'))
                machine_root.mkdir()
                self.machine = FakeMachine(machine_root)
                if missing == 'splash':
                    (machine_root / 'proc/cmdline').write_text('quiet rw\n')
                elif missing == 'enrolment':
                    (machine_root / 'etc/default').mkdir(parents=True, exist_ok=True)
                    (machine_root / 'etc/default/limine').write_text('ENABLE_ENROLL_LIMINE_CONFIG=yes\n')
                else:
                    (machine_root / missing).unlink()
                self.root, before = machine_root, tree(machine_root)
                with self.assertRaisesRegex(RuntimeError, message):
                    self.converge('shaffy-os')
                self.assertEqual((tree(machine_root), self.machine.calls), (before, []))
                self.assertFalse(self.state.exists())

    def test_without_cachyos_theme_shaffy_os_is_refused(self):
        shutil.rmtree(self.root / THEMES / 'cachyos')
        with self.assertRaisesRegex(RuntimeError, 'cachyos-plymouth-theme'):
            self.converge('shaffy-os')
        self.assertFalse((self.root / THEMES / 'shaffy-os').exists())

    def test_an_interrupted_rebuild_is_resumed_by_the_next_apply(self):
        self.machine.fail = True
        with self.assertRaisesRegex(RuntimeError, 'limine-mkinitcpio failed'):
            self.converge('shaffy-os')
        self.assertEqual(self.record(), {'selected': 'shaffy-os', 'rebuild': True})
        self.machine.fail, self.machine.calls = False, []
        self.assertEqual(self.converge('shaffy-os'), (True, []))
        self.assertEqual(self.machine.rebuilds(), 1)
        self.assertEqual(self.record()['rebuild'], False)
        self.assertEqual(self.converge('shaffy-os'), (False, []))

    def test_a_kernel_limine_mkinitcpio_skipped_is_rebuilt_by_the_next_apply(self):
        self.machine.skip = True
        with self.assertRaisesRegex(RuntimeError, 'mkinitcpio failed for kernel 6.18.1-1'):
            self.converge('shaffy-os')
        self.machine.skip = False
        self.assertEqual(self.converge('shaffy-os'), (True, []))
        self.assertEqual(self.machine.rebuilds(), 2)

    def test_a_selection_that_does_not_read_back_is_not_built(self):
        self.machine.reads = 'bgrt'
        with self.assertRaisesRegex(RuntimeError, 'reads bgrt, not shaffy-os'):
            self.converge('shaffy-os')
        self.assertEqual(self.machine.rebuilds(), 0)
        self.assertTrue(self.record()['rebuild'])

    def test_a_theme_selected_by_hand_stays_until_the_setting_names_it(self):
        conf = self.root / 'etc/plymouth/plymouthd.conf'
        conf.write_text(INSTALLED_CONF.replace('cachyos-bootanimation', 'custom'))
        before = tree(self.root)
        with self.assertRaisesRegex(RuntimeError, 'shows the theme custom.*desktop.boot_splash: custom'):
            self.converge('shaffy-os')
        self.assertEqual((tree(self.root), self.machine.calls), (before, []))
        self.assertEqual(self.converge('custom'), (False, []))  # named by the setting, it becomes Shaffy OS's
        self.assertEqual(self.machine.calls, [])
        self.assertEqual(self.record(), {'selected': 'custom'})
        self.assertEqual(self.converge('shaffy-os'), (True, []))
        self.assertIn('Theme=shaffy-os\n', self.conf())

    def test_a_cachyos_update_reaches_the_copies_and_is_rebuilt(self):
        self.converge('shaffy-os')
        base = self.root / THEMES / 'cachyos'
        (base / 'lock.png').write_text('cachyos lock.png, updated')
        (base / 'throbber-0003.png').unlink()
        (self.root / THEMES / 'shaffy-os/stale.png').write_text('stale')
        (self.root / THEMES / 'shaffy-os/entry.png').unlink()
        (self.root / THEMES / 'shaffy-os/entry.png').symlink_to(base / 'entry.png')
        self.machine.calls = []
        self.assertEqual(self.converge('shaffy-os'), (True, []))
        installed = self.installed()
        self.assertEqual(installed['lock.png'].read_text(), 'cachyos lock.png, updated')
        self.assertFalse(installed['entry.png'].is_symlink())
        self.assertNotIn('throbber-0003.png', installed)
        self.assertNotIn('stale.png', installed)
        self.assertEqual(self.machine.rebuilds(), 1)
        copied = {
            p.name: p.read_text() for p in self.state.glob('boot-splash-copies/*/usr/share/plymouth/themes/shaffy-os/*')
        }
        self.assertEqual(copied['lock.png'], 'cachyos lock.png')
        self.assertEqual(copied['stale.png'], 'stale')

    @host_test(
        "builds an image folder with mkinitcpio's own add_full_dir from the installed CachyOS theme",
        tools=('bash',),
        paths=('/usr/lib/initcpio/functions', '/usr/share/plymouth/themes/cachyos/cachyos.plymouth'),
    )
    def test_mkinitcpio_puts_every_image_two_step_needs_into_the_initramfs(self):
        shutil.rmtree(self.root / THEMES / 'cachyos')
        shutil.copytree('/usr/share/plymouth/themes/cachyos', self.root / THEMES / 'cachyos')
        self.converge('shaffy-os')
        folder, build = self.root / THEMES / 'shaffy-os', self.root.parent / 'build'
        script = 'source /usr/lib/initcpio/functions; quiet() { :; }; add_full_dir "$1"'
        subprocess.run(['bash', '-c', script, 'bash', str(folder)], env={'BUILDROOT': str(build)}, check=True)
        image = build / folder.relative_to('/')
        for name in [*NEEDED, 'throbber-0001.png', 'animation-0001.png']:
            with self.subTest(name=name):
                self.assertTrue((image / name).is_file() and not (image / name).is_symlink(), name)
                self.assertEqual((image / name).read_bytes(), (folder / name).read_bytes())


@unittest.skipUnless(shutil.which('mise'), 'needs the Ansible controller')
class BootSplashRoleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        # The role's own tasks, without the settings role it depends on and the package install.
        role = self.tmp / 'boot_splash'
        shutil.copytree(ROOT / 'roles/boot_splash', role)
        (role / 'meta/main.yml').write_text('---\ndependencies: []\n')
        tasks = yaml.safe_load((role / 'tasks/main.yml').read_text())
        (role / 'tasks/main.yml').write_text(
            yaml.safe_dump([t for t in tasks if 'community.general.pacman' not in t], sort_keys=False)
        )
        self.role = role
        self.fake = self.tmp / 'configure-boot-splash.py'
        self.fake.write_text(
            "import sys\nprint('args:', *sys.argv[1:])\nprint('changed:', int('--apply' in sys.argv))\n"
        )

    def play(self, theme, check=False):
        play = self.tmp / 'play.yml'
        settings = {'settings': {'desktop': {'boot_splash': theme}}, 'packages': {'groups': {'boot_splash': []}}}
        play.write_text(
            yaml.safe_dump(
                [
                    {
                        'hosts': 'localhost',
                        'connection': 'local',
                        'gather_facts': False,
                        'vars': {
                            'ansible_python_interpreter': '/usr/bin/python3',
                            'shaffy': settings,
                            'boot_splash_script': str(self.fake),
                            'boot_splash_state_dir': str(self.tmp / 'state'),
                        },
                        'roles': [{'role': str(self.role)}],
                    }
                ]
            )
        )
        arguments = ['-i', 'localhost,', *(['--check'] if check else []), '-v', str(play)]
        command, env = bootstrap.ansible_command(ROOT, 'ansible-playbook', *arguments)
        return subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True, timeout=90, check=False)

    def test_the_role_passes_the_setting_and_reports_the_scripts_change(self):
        for theme, check, expected in (
            ('shaffy-os', False, '--apply --theme shaffy-os --state-dir {tmp}/state'),
            ('spinner', True, '--check --theme spinner --state-dir {tmp}/state'),
        ):
            with self.subTest(theme=theme, check=check):
                result = self.play(theme, check=check)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('args: ' + expected.format(tmp=self.tmp), result.stdout)
                self.assertRegex(result.stdout, r'changed=0\s' if check else r'changed=1\s')

    def test_false_leaves_plymouth_alone(self):
        result = self.play(False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Leave the boot splash alone', result.stdout)
        self.assertNotIn('args:', result.stdout)  # neither the package nor the script task ran


if __name__ == '__main__':
    unittest.main()
