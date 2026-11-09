#!/usr/bin/python3
"""Prepare disk swap and resume for hibernation from the supplement's setting; default to a check.

CachyOS swaps to zram, which cannot hold a hibernation image. With a size, this script makes a
top-level Btrfs subvolume @swap mounted at /swap, outside snapper's configurations, a swap file
of that size at a lower priority than zram, and a drop-in for CachyOS's limine-entry-tool that
adds resume= and resume_offset= to the kernels' command line. It regenerates the Limine entries
with limine-mkinitcpio, as a kernel update does, reads each entry back and checks that the
loader holds the new configuration's checksum and signature. An @swap whose swap
file already has that size is adopted. Without a size, it removes its drop-in and fstab lines
and switches its swap file off, but keeps the file and the subvolume. It refuses a resume it did
not configure itself, and it never reboots or touches Secure Boot keys. Of /etc/default/limine it
changes one thing only, and only when nothing else sets the default command line: a
KERNEL_CMDLINE[default]= , which would replace what the drop-in adds, becomes += .
"""

import argparse
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

GIB = 1024**3
# Left free beside a new swap file, so that Btrfs keeps room for its metadata.
MARGIN = 4 * GIB
SUBVOLUME = '@swap'
MOUNT = '/swap'
FILE = '/swap/swapfile'
# Below the 100 of cachyos-settings' zram-generator.conf, so that zram stays the everyday swap.
PRIORITY = 10
DROPIN = '/etc/limine-entry-tool.d/90-shaffy-hibernation.conf'
HEADER = "# Managed by Shaffy OS (the power role): power.hibernation's resume from /swap/swapfile.\n"
BEGIN = '# BEGIN Managed by Shaffy OS (the power role): power.hibernation\n'
END = '# END Managed by Shaffy OS (the power role): power.hibernation\n'
RESUME = ('resume=', 'resume_offset=')
DEFAULT_LINE = re.compile(r'\s*KERNEL_CMDLINE\[default\]\s*\+?=')
# Where CachyOS's Limine tooling installs the loader on the EFI system partition (x86_64), and the lock
# its tools hold while they change that partition.
LOADER = 'EFI/limine/limine_x64.efi'
LOCK = '/run/lock/boot-partition.lock'
# What a loader problem after a rebuild asks of the person; the next apply rebuilds and checks again.
UNBOOTABLE = (
    'Do not restart until it is solved: sudo limine-mkinitcpio shows what its boot hooks report, and with '
    'Secure Boot, sudo /usr/bin/python3 -I scripts/configure-secure-boot.py shows the boot chain. The next '
    'apply rebuilds the boot images and checks them again'
)
# The files the entry tool reads, in its order (load_config in /usr/lib/limine/limine-common-functions).
LIMINE_CONFIG = (
    '/usr/share/limine-entry-tool.d/*.conf',
    '/etc/limine-entry-tool.conf',
    '/etc/limine-entry-tool.d/*.conf',
    '/etc/default/limine',
)


def run(*command):
    return subprocess.run(command, capture_output=True, text=True, check=True, env={**os.environ, 'LC_ALL': 'C'}).stdout


def tokens(line):
    """Kernel parameters; a double-quoted value with spaces stays one."""
    return re.findall(r'(?:[^\s"]|"[^"]*")+', line)


def resume_of(parameters):
    return sorted(p for p in parameters if p.startswith(RESUME))


def without_resume(parameters):
    return sorted(p for p in parameters if not p.startswith(RESUME))


def dropin_tokens(text):
    return sorted(re.findall(r'(?<![\w.])resume(?:_offset)?=[^\s"]+', text or ''))


def active_lines(path):
    """The lines of a configuration file that are not comments."""
    return [line for line in path.read_text().splitlines() if not line.lstrip().startswith('#')]


def write(path, text):
    """Replace a file whole, keeping its mode: an interrupted write leaves the old one."""
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    temporary = path.with_name(f'.{path.name}.new')
    with open(temporary, 'w') as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def fstab_block(uuid):
    # nofail: a missing subvolume must not stop the boot; resume reads the disk before any mount.
    return (
        BEGIN
        + f'UUID={uuid} {MOUNT} btrfs subvol=/{SUBVOLUME},noatime,nofail 0 0\n'
        + f'{FILE} none swap defaults,pri={PRIORITY} 0 0\n'
        + END
    )


def split_fstab(text):
    """(fstab without this script's block, the block's start and end or None)."""
    if text.count(BEGIN) != text.count(END) or text.count(BEGIN) > 1 or text.find(BEGIN) > text.find(END):
        raise RuntimeError(f'/etc/fstab: the lines between "{BEGIN.strip()}" and its END are malformed')
    if BEGIN not in text:
        return text, None
    start, stop = text.index(BEGIN), text.index(END) + len(END)
    return text[:start] + text[stop:], (start, stop)


def kernel_entries(text, machine_id, names):
    """(title, parameters) of each Linux entry directly under this machine's OS entry in limine.conf: the
    one the entry tool marks with the machine-id, else the one titled with a name it gives the OS. The
    snapshot entries below belong to limine-snapper-sync."""
    entries = []
    for line in (raw.strip() for raw in text.splitlines()):
        heading = re.fullmatch(r'(/+)\+?(.*)', line)
        if heading:
            entries.append((len(heading[1]), heading[2].strip(), {}))
        elif entries and ':' in line and not line.startswith('#'):
            key, value = line.split(':', 1)
            entries[-1][2].setdefault(key.strip().lower(), []).append(value.strip())

    def under(chosen):
        found, inside = [], False
        for depth, title, options in entries:
            if depth == 1:
                inside = chosen(title, options)
            elif inside and depth == 2 and options.get('protocol') == ['linux']:
                found.append((title, tokens(' '.join(options.get('cmdline', []) + options.get('kernel_cmdline', [])))))
        return found

    marked = under(lambda title, options: any(f'machine-id={machine_id}' in c for c in options.get('comment', [])))
    return marked or under(lambda title, options: title in names)


def limine_settings(path):
    """KEY=value settings as CachyOS's Limine tools read them, the last one winning; they take
    ENABLE_ENROLL_LIMINE_CONFIG from /etc/default/limine only. `path` maps a name to the root this runs on.
    configure-hibernation.py and configure-boot-splash.py hold this function and the three below alike."""
    values = {}
    for pattern in LIMINE_CONFIG:
        if pattern == '/etc/default/limine':
            values.pop('ENABLE_ENROLL_LIMINE_CONFIG', None)
        folder, _, name = pattern.rpartition('/')
        for file in sorted(p for p in path(folder).glob(name) if p.is_file()):
            for line in file.read_text().splitlines():
                match = re.fullmatch(r'\s*([A-Za-z_]\w*)\s*=\s*(.*)', line)
                if match:
                    values[match[1]] = match[2].removesuffix('"').removeprefix('"')
    return values


def limine_mkinitcpio(path, run, conf, enrolled):
    """Rebuild every kernel's boot image as a kernel update does, and return what limine-mkinitcpio prints.
    Its boot hooks enrol limine.conf's checksum in the loader and sign the loader with sbctl's keys, but
    limine-mkinitcpio fails for a hook only when that hook runs last, so the loader is checked either way."""
    try:
        output, failed = run('limine-mkinitcpio'), None
    except (RuntimeError, subprocess.CalledProcessError) as error:
        output, failed = '', error
    problems = loader_problems(path, run, conf, enrolled)
    if problems:
        reason = (getattr(failed, 'stderr', None) or str(failed)).strip()[-1000:]
        cause = f'limine-mkinitcpio failed ({reason}), and ' if failed else 'after limine-mkinitcpio, '
        raise RuntimeError(f'{cause}{"; ".join(problems)}. {UNBOOTABLE}') from failed
    if failed:
        raise failed
    return output


def loader_problems(path, run, conf, enrolled):
    """What would keep the loader from starting the rebuilt entries as before: limine.conf's checksum, where
    enrolment is on, and its signature, where sbctl has keys."""
    try:
        status = path('/usr/bin/sbctl').is_file() and sbctl_json(run, 'status', '--json')
    except RuntimeError, subprocess.CalledProcessError:
        status = None  # as the boot hooks take it: they sign only when sbctl answers that it has keys
    signing = bool((status or {}).get('installed'))
    if not (enrolled or signing):
        return []
    loader = f'{conf.rsplit("/", 1)[0]}/{LOADER}'
    lock = path(LOCK)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, 'a') as handle:
        # limine-snapper-sync puts the unenrolled, unsigned loader back while it holds this lock.
        fcntl.flock(handle, fcntl.LOCK_EX)
        missing = [name for name in (conf, loader) if not path(name).is_file()]
        if missing:
            return [f'{name} is missing' for name in missing]
        problems = []
        checksum = hashlib.blake2b(path(conf).read_bytes()).hexdigest().encode()
        if enrolled and checksum not in path(loader).read_bytes():
            problems.append(
                f'{loader} does not hold the checksum of {conf}, so Limine would refuse the configuration or '
                'start without checking the boot files'
            )
        if signing:
            found = sbctl_json(run, 'verify', '--json', loader) or []
            if not any(item.get('file_name') == loader and item.get('is_signed') == 1 for item in found):
                problems.append(f"{loader} is not signed with sbctl's keys, so Secure Boot would refuse it")
    return problems


def sbctl_json(run, *arguments):
    """What sbctl prints as JSON. A runner may mix sbctl's error output in, so lines before it are passed over."""
    output = run('sbctl', *arguments)
    lines = output.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.lstrip().startswith(('{', '[', 'null')):
            try:
                return json.JSONDecoder().raw_decode(''.join(lines[index:]).lstrip())[0]
            except ValueError:
                continue
    if output.strip():
        raise RuntimeError(f'sbctl {" ".join(arguments)} printed no JSON:\n{output.strip()[-1000:]}')
    return None


def handover(places):
    return (
        'hibernation is set up outside Shaffy OS, and power.hibernation adds no second resume:\n'
        + ''.join(f'  - {place}\n' for place in places)
        + f'To hand it over, delete those lines and files, keep {MOUNT} mounted and its swap file on, and apply '
        f'again: the subvolume {SUBVOLUME} at {MOUNT} with a swap file {FILE} of exactly power.hibernation.swap_gib '
        "GiB is adopted, and the boot entries are regenerated with Shaffy OS's drop-in. "
        'Otherwise remove power.hibernation from the supplement.'
    )


class Hibernation:
    def __init__(self, root, run, state, apply, free):
        self.root, self.run, self.state, self.apply, self.free = root, run, state, apply, free
        self.changed, self.notes, self.copies = False, [], None
        self.record_path = state / 'hibernation.json'
        self.record = json.loads(self.record_path.read_text()) if self.record_path.exists() else {}

    def path(self, name):
        return self.root / name.lstrip('/')

    def name(self, path):
        return '/' + str(path.relative_to(self.root))

    def act(self, what):
        """Count a change: make it with --apply, only name it with --check."""
        self.changed = True
        if not self.apply:
            self.notes.append(f'would {what}')
        return self.apply

    def keep(self, **changes):
        """Record what this script owns, before the effect that makes it so."""
        self.record = {**self.record, **changes}
        self.state.mkdir(parents=True, exist_ok=True)
        write(self.record_path, json.dumps(self.record, indent=2, sort_keys=True) + '\n')

    def copy(self, name):
        """Keep a copy of a file from before this run changed it."""
        if self.copies is None:
            self.copies = self.state / 'hibernation-copies' / datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
            self.copies.mkdir(parents=True, mode=0o700)  # limine.conf comes from the root-only ESP
        source, target = self.path(name), self.copies / name.lstrip('/')
        if source.exists() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def config_files(self, pattern):
        folder, _, name = pattern.rpartition('/')
        return sorted(p for p in self.path(folder).glob(name) if p.is_file())

    def mount(self, target):
        try:
            found = self.run('findmnt', '--json', '--mountpoint', target, '-o', 'SOURCE,FSTYPE,FSROOT,UUID')
        except subprocess.CalledProcessError:
            return None  # findmnt fails when nothing is mounted there
        return json.loads(found)['filesystems'][-1]

    # What the machine is. Each refusal comes before anything is written.

    def filesystem(self):
        root = self.mount('/')
        if not root or root['fstype'] != 'btrfs':
            kind = root['fstype'] if root else 'not mounted'
            raise RuntimeError(f"/ is {kind}, not Btrfs: power.hibernation needs CachyOS's Btrfs layout")
        device = root['source'].split('[', 1)[0]
        number, kind = self.run('lsblk', '--noheadings', '--nodeps', '--raw', '-o', 'MAJ:MIN,TYPE', device).split()
        if len(list(self.path(f'/sys/fs/btrfs/{root["uuid"]}/devices').iterdir())) != 1:
            raise RuntimeError('the Btrfs filesystem of / spans several devices, where a swap file cannot live')
        return {'device': device, 'uuid': root['uuid'], 'number': number, 'encrypted': kind == 'crypt'}

    def limine(self):
        """limine.conf, once it is clear that CachyOS's entry tool writes it and can rewrite it safely."""
        for tool in ('limine-entry-tool', 'limine-mkinitcpio'):
            if not self.path(f'/usr/bin/{tool}').is_file():
                raise RuntimeError(f"/usr/bin/{tool} is missing: power.hibernation needs Limine with CachyOS's tooling")
        settings = limine_settings(self.path)
        if settings.get('ENABLE_UKI') == 'yes':
            raise RuntimeError('ENABLE_UKI=yes: power.hibernation cannot read the command line inside a unified image')
        hooks = self.path('/etc/boot/hooks/post.d')
        enrols = hooks.is_dir() and any(
            not hook.name.endswith('.disabled')
            and os.access(hook, os.X_OK)
            and 'limine-enroll-config' in (os.readlink(hook) if hook.is_symlink() else hook.name)
            for hook in hooks.iterdir()
        )
        if settings.get('ENABLE_ENROLL_LIMINE_CONFIG') == 'yes' and not enrols:
            raise RuntimeError(
                "Limine's configuration is enrolled (ENABLE_ENROLL_LIMINE_CONFIG=yes), but no hook in "
                '/etc/boot/hooks/post.d/ runs limine-enroll-config: Limine would refuse regenerated entries'
            )
        esp = settings.get('ESP_PATH') or next(
            (c for c in ('/efi', '/boot', '/boot/efi', '/limine') if (self.mount(c) or {}).get('fstype') == 'vfat'), ''
        )
        conf = f'{esp.rstrip("/")}/limine.conf'
        if not esp or not self.path(conf).is_file():
            raise RuntimeError(f'no limine.conf on the EFI system partition ({esp or "not found"})')
        return conf

    def os_names(self):
        """The titles the entry tool gives this machine's OS entry (TARGET_OS_NAME, else /etc/os-release)."""
        release = self.path('/etc/os-release')
        found = dict(
            re.findall(r'(?m)^(PRETTY_NAME|NAME)="?([^"\n]*)"?$', release.read_text() if release.exists() else '')
        )
        return [
            n
            for n in (limine_settings(self.path).get('TARGET_OS_NAME'), found.get('PRETTY_NAME'), found.get('NAME'))
            if n
        ]

    def initramfs(self, encrypted):
        hooks = []
        for path in self.config_files('/etc/mkinitcpio.conf') + self.config_files('/etc/mkinitcpio.conf.d/*.conf'):
            for operator, items in re.findall(r'(?m)^\s*HOOKS\s*(\+?)=\s*\(([^)]*)\)', '\n'.join(active_lines(path))):
                hooks = (hooks if operator else []) + items.replace('"', '').split()
        missing = [hook for hook in ('systemd', 'sd-encrypt')[: 2 if encrypted else 1] if hook not in hooks]
        if missing:
            raise RuntimeError(
                f"mkinitcpio's HOOKS lack {' and '.join(missing)}: power.hibernation needs CachyOS's "
                'systemd-based initramfs, which finds the resume device itself'
            )

    def cmdline_reaches(self):
        """Refuse when the drop-in's KERNEL_CMDLINE[default]+= would not reach the kernels' command line.
        Return /etc/default/limine when its own = is all that stands in the way: with no other file
        setting the default command line, += there keeps that line as it is and lets the drop-in add."""
        ours = self.path(DROPIN)
        order = [
            *self.config_files(LIMINE_CONFIG[0]),
            *self.config_files(LIMINE_CONFIG[1]),
            *sorted({*self.config_files(LIMINE_CONFIG[2]), ours}),
            *self.config_files(LIMINE_CONFIG[3]),
        ]
        fix = None
        for path in order[order.index(ours) + 1 :]:
            if any(re.match(r'\s*KERNEL_CMDLINE\[default\]\s*=', line) for line in active_lines(path)):
                others = [
                    other
                    for other in order
                    if other not in (ours, path) and any(DEFAULT_LINE.match(line) for line in active_lines(other))
                ]
                if self.name(path) == '/etc/default/limine' and not others:
                    fix = path
                    continue
                raise RuntimeError(
                    f'{self.name(path)} sets KERNEL_CMDLINE[default]= , which replaces what drop-ins add: change '
                    'that = to +=, as /etc/limine-entry-tool.conf recommends, and apply again. Shaffy OS leaves '
                    f'{self.name(path)} to you'
                )
        if not any(DEFAULT_LINE.match(line) for path in order if path != ours for line in active_lines(path)):
            raise RuntimeError(
                'nothing in /etc/default/limine or a drop-in sets KERNEL_CMDLINE[default]: the entry tool takes the '
                "command line from /etc/kernel/cmdline or /proc/cmdline, and a drop-in's += would replace it"
            )
        return fix

    def kernels(self):
        """The kernel packages, named as limine-mkinitcpio-install names their entries."""
        names, modules = set(), self.path('/usr/lib/modules')
        for version in sorted(p.name for p in modules.iterdir()) if modules.is_dir() else []:
            if (modules / version / 'modules.builtin').is_file():
                try:
                    names.add(self.run('pacman', '-Qqo', f'/usr/lib/modules/{version}/modules.builtin').strip())
                except subprocess.CalledProcessError:
                    continue  # no package's kernel; the tooling skips it too
        if not names:
            raise RuntimeError('no kernel package is installed under /usr/lib/modules')
        return sorted(names)

    def configured(self, kernel):
        """The command line the entry tool gives a kernel's entry. It may print notes first, so the last
        line is the command line (limine-mkinitcpio-install reads it with tail -n 1). A wrong argument
        prints usage with status 0, so callers check the parameters, never only the status."""
        lines = self.run('limine-entry-tool', '--get-cmdline', kernel).strip().splitlines()
        return tokens(lines[-1]) if lines else []

    def swap_file(self):
        """None without a file; else its size, mode, owner and resume offset (None if it is no swap file)."""
        path = self.path(FILE)
        if not os.path.lexists(path):
            return None
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f'{FILE} is not a regular file')
        try:
            swap = self.run('blkid', '-p', '-s', 'TYPE', '-o', 'value', FILE).strip() == 'swap'
            offset = int(self.run('btrfs', 'inspect-internal', 'map-swapfile', '-r', FILE)) if swap else None
        except subprocess.CalledProcessError, ValueError:
            offset = None
        return {'size': info.st_size, 'mode': stat.S_IMODE(info.st_mode), 'owner': info.st_uid, 'offset': offset}

    def usable(self, file, size):
        """Whether the swap file serves as it is. Refuses a file this script may not replace: any but its own
        unfinished one, recorded as `creating` while mkswapfile runs."""
        if file is None:
            return False
        if file['offset'] is not None and file['size'] == size * GIB and file['owner'] == os.geteuid():
            return True
        if self.record.get('creating'):
            return False
        if file['offset'] is None:
            raise RuntimeError(f'{FILE} is no swap file that Btrfs can resume from (blkid, map-swapfile)')
        if file['owner'] != os.geteuid():
            raise RuntimeError(f'{FILE} belongs to uid {file["owner"]}, not root')
        raise RuntimeError(
            f'{FILE} has {file["size"] / GIB:g} GiB, not power.hibernation.swap_gib {size}, and an active swap file '
            f'is never resized. Set {file["size"] / GIB:g}, or remove power.hibernation, apply, delete {FILE}, set '
            'the new size and apply again'
        )

    def room(self, size):
        free = self.free(self.path('/'))
        if free < size * GIB + MARGIN:
            raise RuntimeError(f'/ has {free / GIB:.1f} GiB free; a {size} GiB swap file needs {size + MARGIN // GIB}')

    def active(self):
        return [line.split()[0] for line in self.path('/proc/swaps').read_text().splitlines()[1:] if line.strip()]

    def resumes_from(self, fs, offset):
        """Whether the running kernel resumes from this swap file: the device and offset it was given at boot."""
        return (
            offset is not None
            and self.path('/sys/power/resume').read_text().strip() == fs['number']
            and self.path('/sys/power/resume_offset').read_text().strip() == str(offset)
        )

    def foreign(self, fs, base, offset):
        """Where resume or this swap is configured by something other than this script."""
        places = []
        for pattern in (*LIMINE_CONFIG, '/etc/kernel/cmdline'):
            for path in self.config_files(pattern):
                if path == self.path(DROPIN) and path.read_text().startswith(HEADER):
                    continue
                if any(re.search(r'(?<![\w.-])resume(?:_offset)?=', line) for line in active_lines(path)):
                    places.append(f'{self.name(path)} sets resume= or resume_offset=')
        for line in base.splitlines():
            fields = line.split()
            if len(fields) > 1 and not fields[0].startswith('#'):
                if fields[1] == MOUNT:
                    places.append(f'/etc/fstab mounts {MOUNT}')
                if fields[0] == FILE:
                    places.append(f'/etc/fstab switches on {FILE}')
        running = resume_of(tokens(self.path('/proc/cmdline').read_text()))
        if running and not self.resumes_from(fs, offset):
            places.append(
                f'the running boot has {" ".join(running)} (/proc/cmdline), which is not {FILE}: '
                'remove it from the boot configuration and reboot'
            )
        return places

    def entry_problems(self, conf, ours, present):
        """What keeps each kernel entry of this machine from holding exactly `ours` (or, after removal, none of it)."""
        machine_id = self.path('/etc/machine-id').read_text().strip()
        found = kernel_entries(self.path(conf).read_text(), machine_id, self.os_names())
        if not found:
            return [f'no kernel entry of this machine in {conf}']
        problems = []
        for title, parameters in found:
            got = resume_of(parameters)
            if (got != ours) if present else bool(set(got) & set(ours)):
                problems.append(f'{title} has {" ".join(got) or "no resume"}')
        return problems

    # What it changes.

    def replace_dropin(self, text, kernels):
        """Write the drop-in, or remove it with text None, and check that the entry tool now gives each kernel
        the new resume and otherwise the same command line; else put the old drop-in back."""
        path = self.path(DROPIN)
        previous = path.read_text() if path.exists() else None
        pending = self.record.get('regenerate', False)
        old, new = dropin_tokens(previous), dropin_tokens(text)
        before = {kernel: self.configured(kernel) for kernel in kernels}
        self.copy(DROPIN)
        self.keep(regenerate=True, resume=new or old)
        try:
            if text is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                write(path, text)
            for kernel, parameters in before.items():
                after = self.configured(kernel)
                expected = sorted([p for p in resume_of(parameters) if p not in old] + new)
                if resume_of(after) != expected or without_resume(after) != without_resume(parameters):
                    raise RuntimeError(
                        f'with {DROPIN} {"removed" if text is None else "written"}, limine-entry-tool --get-cmdline '
                        f'{kernel} gives "{" ".join(after)}", not "{" ".join(without_resume(parameters) + expected)}"; '
                        'the drop-in is put back and the entries are left alone'
                    )
        except BaseException:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                write(path, previous)
            self.keep(regenerate=pending)
            raise

    def regenerate(self, conf, ours, present):
        """Rebuild the entries as a kernel update does: limine-mkinitcpio runs the boot hooks, which re-enrol
        Limine's configuration and sign the loader where Secure Boot is set up. It skips a kernel it fails to
        build without failing, so the entries are read back."""
        self.copy(conf)
        self.keep(regenerate=True)
        enrolled = limine_settings(self.path).get('ENABLE_ENROLL_LIMINE_CONFIG') == 'yes'
        limine_mkinitcpio(self.path, self.run, conf, enrolled)
        problems = self.entry_problems(conf, ours, present)
        if problems:
            raise RuntimeError(
                f'after limine-mkinitcpio, {conf}: {"; ".join(problems)}. Read the entries before rebooting; '
                'the next apply regenerates them again'
            )
        self.keep(regenerate=False)

    def mount_swap(self, fs):
        self.path(MOUNT).mkdir(exist_ok=True)
        self.run('mount', '-t', 'btrfs', '-o', f'subvol=/{SUBVOLUME},noatime', f'UUID={fs["uuid"]}', MOUNT)

    def create_subvolume(self, fs):
        # @ and its siblings live in the top level (subvolume 5), mounted only for this.
        top = Path(tempfile.mkdtemp(prefix='shaffy-btrfs-', dir=self.path('/run')))
        try:
            self.run('mount', '-t', 'btrfs', '-o', 'subvolid=5', fs['device'], str(top))
            try:
                self.run('btrfs', 'subvolume', 'create', str(top / SUBVOLUME))
            finally:
                self.run('umount', str(top))
        finally:
            top.rmdir()

    def create_file(self, size):
        self.keep(creating=True, swap_gib=size)
        self.run('btrfs', 'filesystem', 'mkswapfile', '--size', f'{size}G', FILE)
        os.chmod(self.path(FILE), 0o600)
        file = self.swap_file()
        if not self.usable(file, size):
            raise RuntimeError(f'btrfs filesystem mkswapfile made no usable {size} GiB {FILE}')
        self.keep(creating=False)
        return file

    def setup(self, size):
        fs = self.filesystem()
        if self.record.get('filesystem', fs['uuid']) != fs['uuid']:
            raise RuntimeError(f'{self.record_path} records another filesystem than the one of /')
        conf = self.limine()
        self.initramfs(fs['encrypted'])
        default = self.cmdline_reaches()
        kernels = self.kernels()
        fstab = self.path('/etc/fstab').read_text()
        base, block = split_fstab(fstab)
        mounted = self.mount(MOUNT)
        expected = {'fstype': 'btrfs', 'uuid': fs['uuid'], 'fsroot': f'/{SUBVOLUME}'}
        if mounted and any(mounted[key] != value for key, value in expected.items()):
            raise RuntimeError(f'{MOUNT} is mounted from {mounted["source"]}, not from the subvolume {SUBVOLUME} of /')
        file = self.swap_file() if mounted else None
        places = self.foreign(fs, base, file and file['offset'])
        if places:
            raise RuntimeError(handover(places))
        for path in self.config_files('/etc/snapper/configs/*'):
            if any(re.fullmatch(r'\s*SUBVOLUME=["\']?/swap/?["\']?\s*', line) for line in active_lines(path)):
                raise RuntimeError(f'{self.name(path)} takes snapshots of {MOUNT}, where a swap file cannot be')
        exists = bool(mounted)
        if not mounted:
            folder = self.path(MOUNT)
            if folder.is_symlink() or (folder.exists() and (not folder.is_dir() or any(folder.iterdir()))):
                raise RuntimeError(f'{MOUNT} is not mounted and is not an empty folder')
            listed = self.run('btrfs', 'subvolume', 'list', '/')
            exists = bool(re.search(rf'(?m)^ID \d+ gen \d+ top level 5 path {SUBVOLUME}$', listed))
        if (mounted or not exists) and not self.usable(file, size):
            self.room(size)

        adopting = exists and not mounted and not self.record
        if adopting and self.act(f'mount the existing subvolume {SUBVOLUME} at {MOUNT} and adopt its swap file'):
            # A subvolume from before: look inside before taking it over.
            self.mount_swap(fs)
            mounted, file = True, self.swap_file()
            try:
                self.usable(file, size)
            except RuntimeError:
                self.run('umount', MOUNT)
                raise
        if not self.record.get('filesystem') and self.act(f'adopt {FILE}' if file else f'record {self.record_path}'):
            self.keep(filesystem=fs['uuid'], swap_gib=size)
        if not exists and self.act(f'create the subvolume {SUBVOLUME}'):
            self.create_subvolume(fs)
        if not mounted and not adopting and self.act(f'mount {SUBVOLUME} at {MOUNT}'):
            self.mount_swap(fs)
            mounted, file = True, self.swap_file()
        if mounted and self.usable(file, size):
            if self.apply and self.record.get('creating'):
                self.keep(creating=False)
            if file['mode'] != 0o600 and self.act(f'make {FILE} readable by root only'):
                os.chmod(self.path(FILE), 0o600)
        elif mounted:
            if file:  # this script's own unfinished file, which usable() let through
                if FILE in self.active():
                    raise RuntimeError(f'{FILE} is unfinished but switched on; switch it off and apply again')
                if self.act(f'remove the unfinished {FILE}'):
                    self.path(FILE).unlink()
            self.room(size)
            if self.act(f'create the {size} GiB swap file {FILE}'):
                file = self.create_file(size)
        elif not exists:
            self.act(f'create the {size} GiB swap file {FILE}')
        if FILE not in self.active() and self.act(f'switch on {FILE} at priority {PRIORITY}'):
            self.run('swapon', '--priority', str(PRIORITY), FILE)
        wanted = (
            fstab[: block[0]] + fstab_block(fs['uuid']) + fstab[block[1] :]
            if block
            else fstab.rstrip('\n') + '\n\n' + fstab_block(fs['uuid'])
        )
        if wanted != fstab and self.act(f'add {MOUNT} and {FILE} to /etc/fstab'):
            self.copy('/etc/fstab')
            write(self.path('/etc/fstab'), wanted)
            self.run('systemctl', 'daemon-reload')

        if default and self.act('change KERNEL_CMDLINE[default]= to += in /etc/default/limine'):
            self.copy('/etc/default/limine')
            write(default, re.sub(r'(?m)^(\s*KERNEL_CMDLINE\[default\]\s*)=', r'\1+=', default.read_text()))
        offset = file['offset'] if file else None
        if offset is None:
            self.act(f'write {DROPIN} and regenerate the Limine entries')
            return
        ours = [f'resume=UUID={fs["uuid"]}', f'resume_offset={offset}']
        text = HEADER + f'KERNEL_CMDLINE[default]+="{" ".join(ours)}"\n'
        path = self.path(DROPIN)
        if (path.read_text() if path.exists() else None) != text:
            if self.act(f'write {DROPIN} with {" ".join(ours)}'):
                self.replace_dropin(text, kernels)
        else:
            for kernel in kernels:
                got = resume_of(self.configured(kernel))
                if got != ours:
                    raise RuntimeError(
                        f'limine-entry-tool --get-cmdline {kernel} gives {" ".join(got) or "no resume"}, not '
                        f'{" ".join(ours)}: another setting overrides {DROPIN}'
                    )
        problems = self.entry_problems(conf, ours, present=True)
        if (self.record.get('regenerate') or problems) and self.act('regenerate the Limine entries'):
            self.regenerate(conf, ours, present=True)
        memory = int(re.search(r'(?m)^MemTotal:\s+(\d+) kB', self.path('/proc/meminfo').read_text())[1]) * 1024
        if size * GIB < memory:
            self.notes.append(f'{FILE} ({size} GiB) is smaller than the memory ({memory / GIB:.1f} GiB)')
        if not self.resumes_from(fs, offset):
            self.notes.append(f'this boot does not resume from {FILE} yet: reboot, then test a hibernation')

    def remove(self):
        fstab = self.path('/etc/fstab').read_text()
        _, block = split_fstab(fstab)
        path = self.path(DROPIN)
        dropin = path.read_text() if path.exists() else None
        mine = bool(dropin and dropin.startswith(HEADER))
        swap = bool(self.record or block)
        if not (swap or mine):
            return
        if mine or self.record.get('regenerate'):
            conf = self.limine()
            ours = dropin_tokens(dropin) if mine else self.record.get('resume', [])
            if mine and self.act(f'remove {DROPIN}'):
                self.replace_dropin(None, self.kernels())
            problems = self.entry_problems(conf, ours, present=False)
            if (self.record.get('regenerate') or problems) and self.act('regenerate the Limine entries'):
                self.regenerate(conf, ours, present=False)
        if swap and FILE in self.active() and self.act(f'switch off {FILE}'):
            self.run('swapoff', FILE)
        if block and self.act(f'remove {MOUNT} and {FILE} from /etc/fstab'):
            self.copy('/etc/fstab')
            write(self.path('/etc/fstab'), fstab[: block[0]].rstrip('\n') + '\n' + fstab[block[1] :])
            self.run('systemctl', 'daemon-reload')
        if self.record and self.act(f'forget {self.record_path}'):
            self.record_path.unlink()
        if swap:
            self.notes.append(
                f'{FILE} and the subvolume {SUBVOLUME} stay, as they may hold a hibernation image; '
                'delete them once none is needed (docs/POWER-MANAGEMENT.md)'
            )


def converge(size, state, apply, run=run, root=Path('/'), free=lambda path: shutil.disk_usage(path).free):
    """Bring swap and resume to `size` GiB (or remove them, keeping the file, with None); return whether
    anything changed, or with apply=False would change, and the notes to print."""
    hibernation = Hibernation(root, run, state, apply, free)
    if size is None:
        hibernation.remove()
    else:
        hibernation.setup(size)
    return hibernation.changed, hibernation.notes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='report what would change; the default')
    mode.add_argument('--apply', action='store_true', help='make the swap file, the resume and the entries')
    parser.add_argument('--swap-gib', type=int, help='swap file size in GiB; leave out to remove the setup')
    parser.add_argument('--state-dir', type=Path, default=Path('/var/lib/shaffy'))
    args = parser.parse_args(argv)
    if args.swap_gib is not None and args.swap_gib < 1:
        parser.error('--swap-gib must be at least 1')
    changed, notes = converge(args.swap_gib, args.state_dir, args.apply)
    for note in notes:
        print(f'note: {note}')
    print(f'changed: {int(changed)}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as error:
        sys.exit(f'configure-hibernation: {error}: {(error.stderr or error.stdout or "").strip()}')
    except (OSError, RuntimeError) as error:
        sys.exit(f'configure-hibernation: {error}')
