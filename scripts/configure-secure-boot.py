#!/usr/bin/python3
"""Set up Secure Boot with this machine's own keys, in steps run by hand as root; default to a check.

It follows the CachyOS guide (wiki.cachyos.org, Secure Boot setup) for Limine, as Limine 12 documents
it in /usr/share/doc/limine/USAGE.md:

--check    reports and changes nothing: Secure Boot and Setup Mode, sbctl's keys and the ones the
           firmware holds, the Limine loader's signature, the configuration checksum enrolled in it,
           the hashes in limine.conf, kernel lockdown and whether hibernation can still resume.
--prepare  needs the firmware in Setup Mode, or already holding this machine's keys. It overrides
           sbctl's mkinitcpio hook so that kernel images stay as Limine hashed them, creates the keys
           with sbctl, turns on configuration enrolment and file verification in /etc/default/limine,
           hashes the boot menu's wallpaper, and rebuilds the boot images with limine-mkinitcpio, whose
           hooks enrol limine.conf in the loader and sign it. With fwupd installed, it signs fwupd's EFI
           program and stops fwupd from looking for shim, so firmware capsule updates keep working.
--enroll   enrols the keys in the firmware with sbctl, with Microsoft's keys unless --no-microsoft.

Each write step refuses before writing unless the machine is the supported baseline (Limine with
CachyOS's entry tool and boot hooks, the EFI system partition at /boot, a systemd-based initramfs),
copies each file before changing it to /var/lib/shaffy/secure-boot/before-<time>/ and records what it
did there; a repeated step changes nothing. It never deletes keys, never passes --firmware-builtin,
never clears the TPM, never reboots and never forces past sbctl's safeguards. No snapshot restores
firmware keys: turning Secure Boot off in the firmware's menu is the way back.
"""

import argparse
import base64
from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import stat
import subprocess
import sys

STATE = Path('/var/lib/shaffy/secure-boot')
ESP = '/boot'
CONFIG = '/boot/limine.conf'
LOADER = '/boot/EFI/limine/limine_x64.efi'
WINDOWS = '/boot/EFI/Microsoft/Boot/bootmgfw.efi'
# What the firmware starts when it has no entry for Limine; limine-install leaves it unsigned.
FALLBACK = '/boot/EFI/BOOT/BOOTX64.EFI'
# Configuration files Limine reads before /boot/limine.conf when they exist (CONFIG.md, "Location of the
# config file"); their checksum would not be the enrolled one.
SHADOWING = (
    '/boot/EFI/limine/limine.conf',
    '/boot/EFI/BOOT/limine.conf',
    '/boot/boot/limine/limine.conf',
    '/boot/boot/limine.conf',
    '/boot/limine/limine.conf',
)
DEFAULTS = '/etc/default/limine'
# mkinitcpio runs a post hook from /etc/initcpio/post in place of one of the same name in
# /usr/lib/initcpio/post, where sbctl installs the hook that signs each kernel image.
KERNEL_HOOK = '/etc/initcpio/post/sbctl'
KEYS = '/var/lib/sbctl/keys'
SBCTL_CONFIG = '/etc/sbctl/sbctl.conf'
FWUPD_EFI = '/usr/lib/fwupd/efi/fwupdx64.efi'
FWUPD_SIGNED = FWUPD_EFI + '.signed'
FWUPD_CONFIG = '/etc/fwupd/fwupd.conf'
# The lock CachyOS's Limine tools take while they change the EFI system partition.
LOCK = '/run/lock/boot-partition.lock'
BEGIN = '# BEGIN Managed by Shaffy OS: Secure Boot\n'
END = '# END Managed by Shaffy OS: Secure Boot\n'
# ENABLE_UKI=no because the kernel hook below leaves images unsigned, which a unified kernel image,
# started by the firmware, cannot be.
SETTINGS = {'ENABLE_ENROLL_LIMINE_CONFIG': 'yes', 'ENABLE_VERIFICATION': 'yes', 'ENABLE_UKI': 'no'}
# CachyOS has no shim; without this, fwupd refuses capsule updates while Secure Boot is on.
FWUPD_SETTINGS = {'DisableShimForSecureBoot': 'true'}
KERNEL_HOOK_TEXT = """#!/bin/sh
# Managed by Shaffy OS (scripts/configure-secure-boot.py): Secure Boot.
# This replaces sbctl's mkinitcpio hook, which would sign each kernel image in place. Limine checks a
# kernel against the BLAKE2B hash in its enrolled limine.conf, not against a signature, so kernel
# images stay exactly as their package installed them and as the entry tool hashed them. The
# firmware checks the Limine loader, which CachyOS's boot hooks sign.
exit 0
"""
# The files CachyOS's entry tool reads, in its order (load_config in /usr/lib/limine/limine-common-functions).
LIMINE_CONFIG = (
    '/usr/share/limine-entry-tool.d/*.conf',
    '/etc/limine-entry-tool.conf',
    '/etc/limine-entry-tool.d/*.conf',
    '/etc/default/limine',
)
TOOLS = ('limine', 'limine-entry-tool', 'limine-mkinitcpio', 'limine-enroll-config')
# The boot hooks that put a clean loader back before a regeneration and enrol and sign it after.
BOOT_HOOKS = (('pre', 'limine-reset-enroll'), ('post', 'limine-enroll-config'))
# limine.conf's options that name a file, and the ones Limine skips rather than refuses on a bad hash.
FILE_OPTIONS = ('path', 'kernel_path', 'module_path', 'image_path', 'dtb_path', 'global_dtb', 'wallpaper', 'term_font')
COSMETIC = ('wallpaper', 'term_font')
# Protocols whose files the firmware checks by their signature instead of a hash.
CHAINLOAD = ('efi', 'efi_chainload')
RESOURCE = re.compile(r'\$?boot\(\):/([^#]*)(?:#(.*))?')
HASH = re.compile(r'[0-9a-fA-F]{128}')
HASHED = re.compile(r'.*#[0-9a-fA-F]{128}')
COSMETIC_LINE = re.compile(
    r'(?mi)^(?P<head>[ \t]*(?:wallpaper|term_font)[ \t]*:[ \t]*\$?boot\(\):/)(?P<file>[^#\s]+)(?:#\S*)?[ \t]*$'
)
EFIVARS = '/sys/firmware/efi/efivars'
# The UEFI specification's global variable GUID, under which the firmware keeps SecureBoot and SetupMode.
EFI_GLOBAL = '8be4df61-93ca-11d2-aa0d-00e098032b8c'
CRYPTTAB = ('/etc/crypttab', '/etc/crypttab.initramfs')
# The firmware variables of Secure Boot, kept before an enrolment changes them; all of them public.
TRUST_VARIABLES = ('PK', 'KEK', 'db', 'dbx', 'SecureBoot', 'SetupMode')
TPM_UNLOCK = (
    'the disk unlocks with the TPM (tpm2-device in /etc/crypttab): enrolling keys and turning Secure Boot on '
    'change what the TPM measures, so expect to type the passphrase at the next start, and enrol the TPM '
    'again afterwards with systemd-cryptenroll'
)
NO_MICROSOFT = (
    "Enrolling without Microsoft's keys: the firmware will then refuse everything Microsoft signed, "
    "such as Windows's boot manager and the option ROMs of graphics cards and other devices. A screen "
    'can stay dark at start-up, or the machine may not start at all until Secure Boot is turned off in '
    "the firmware. sbctl looks for such option ROMs in the TPM's event log and refuses when it finds one; "
    'this tool never overrides that.'
)


def run(*command):
    """What a command prints; a failure raises with its errors."""
    done = subprocess.run(command, capture_output=True, text=True, check=False, env={**os.environ, 'LC_ALL': 'C'})
    if done.returncode:
        output = (done.stderr + done.stdout).strip()[-3000:]
        raise RuntimeError(f'{" ".join(command)} failed with status {done.returncode}:\n{output}')
    return done.stdout


def write(path, data, mode=None):
    """Replace a file whole, keeping its mode unless given one: an interrupted write leaves the old one."""
    if mode is None:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    temporary = path.with_name(f'.{path.name}.new')
    with open(temporary, 'wb') as handle:
        handle.write(data.encode() if isinstance(data, str) else data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def assignments(text, section=None):
    """(key, value) of each KEY=value line, as the entry tool and GLib's key files read them; with a
    section, only the lines under its [section] header."""
    found, current = [], None
    for line in text.splitlines():
        header = re.fullmatch(r'\s*\[([^]]*)\]\s*', line)
        if header:
            current = header[1].strip()
            continue
        match = re.fullmatch(r'\s*([A-Za-z_]\w*)\s*=\s*(.*?)\s*', line)
        if match and not line.lstrip().startswith('#') and section in (None, current):
            found.append((match[1], match[2].removeprefix('"').removesuffix('"')))
    return found


def split_block(text, name):
    """(text without Shaffy OS's block, the block's start and end or None); malformed markers raise."""
    if text.count(BEGIN) != text.count(END) or text.count(BEGIN) > 1 or text.find(BEGIN) > text.find(END):
        raise RuntimeError(f'{name}: the lines between "{BEGIN.strip()}" and its END are malformed; fix them by hand')
    if BEGIN not in text:
        return text, None
    start, stop = text.index(BEGIN), text.index(END) + len(END)
    return text[:start] + text[stop:], (start, stop)


def with_block(text, name, settings, body, section=None):
    """`text` with Shaffy OS's block holding `settings`, or unchanged where lines outside it already set
    each of them alike (in `section`, for a key file). Refuses one of those keys set outside the block
    to another value."""
    base, span = split_block(text, name)
    outside = assignments(base, section)
    conflicts = sorted({f'{k}={v}' for k, v in outside if k in settings and v != settings[k]})
    if conflicts:
        wanted = ' '.join(f'{k}={v}' for k, v in settings.items())
        raise RuntimeError(
            f'{name} sets {", ".join(conflicts)} outside the lines Shaffy OS manages, where Secure Boot needs '
            f'{wanted}. Delete those lines, or set them as it needs, and run the step again'
        )
    if span is None and set(settings) <= {k for k, _ in outside}:
        return text
    block = BEGIN + body + END
    if span:
        return text[: span[0]] + block + text[span[1] :]
    return (text.rstrip('\n') + '\n\n' if text.strip() else '') + block


def boot_files(text):
    """(option, value, protocol) of each file limine.conf names; the protocol is None outside an entry."""
    entries = [[None, []]]
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('/'):
            entries.append([None, []])
        elif ':' in line and not line.startswith('#'):
            key, value = (part.strip() for part in line.split(':', 1))
            if key.lower() == 'protocol':
                entries[-1][0] = value.lower()
            elif key.lower() in FILE_OPTIONS:
                entries[-1][1].append((key.lower(), value))
    return [(key, value, protocol) for protocol, options in entries for key, value in options]


def inert(text):
    """Whether a hook does nothing but exit successfully, as the override does in any wording."""
    lines = text.splitlines()
    active = [line.strip() for line in lines[1:] if line.strip() and not line.strip().startswith('#')]
    return bool(lines) and lines[0].startswith('#!') and active == ['exit 0']


def microsoft(keys):
    """Whether the firmware's KEK and db hold Microsoft's certificates."""
    return all(any('Microsoft' in name for _, name in keys[db]) for db in ('KEK', 'DB'))


class SecureBoot:
    def __init__(self, root, run, state):
        self.root, self.run, self.state = root, run, state
        self.notes, self.changes, self.copies, self.digests = [], [], None, {}
        self.record_path = state / 'secure-boot.json'
        self.record = json.loads(self.record_path.read_text()) if self.record_path.exists() else {}

    def path(self, name):
        return self.root / name.lstrip('/')

    def host(self, path):
        return '/' + str(path.relative_to(self.root))

    # What the machine is.

    def mount(self, target):
        try:
            found = self.run('findmnt', '--json', '--mountpoint', target, '-o', 'SOURCE,FSTYPE,FSROOT')
        except RuntimeError:
            return None  # findmnt fails when nothing is mounted there
        return json.loads(found)['filesystems'][-1]

    def config_files(self, pattern):
        folder, _, name = pattern.rpartition('/')
        return sorted(p for p in self.path(folder).glob(name) if p.is_file())

    def limine_settings(self):
        """KEY=value settings as the entry tool reads them.

        ENABLE_ENROLL_LIMINE_CONFIG only from /etc/default/limine.
        """
        values = {}
        for pattern in LIMINE_CONFIG:
            if pattern == DEFAULTS:
                values.pop('ENABLE_ENROLL_LIMINE_CONFIG', None)
            for path in self.config_files(pattern):
                values.update(assignments(path.read_text()))
        return values

    def initramfs_hooks(self):
        hooks = []
        for path in self.config_files('/etc/mkinitcpio.conf') + self.config_files('/etc/mkinitcpio.conf.d/*.conf'):
            active = '\n'.join(line for line in path.read_text().splitlines() if not line.lstrip().startswith('#'))
            for operator, items in re.findall(r'(?m)^\s*HOOKS\s*(\+?)=\s*\(([^)]*)\)', active):
                hooks = (hooks if operator else []) + items.replace('"', '').split()
        return hooks

    def boot_hook(self, kind, target):
        folder = self.path(f'/etc/boot/hooks/{kind}.d')
        return folder.is_dir() and any(
            not hook.name.endswith('.disabled')
            and os.access(hook, os.X_OK)
            and target in (os.readlink(hook) if hook.is_symlink() else hook.name)
            for hook in folder.iterdir()
        )

    def baseline(self):
        """Why this machine is not the supported baseline; empty when it is."""
        if not self.path('/sys/firmware/efi').is_dir():
            return ['the machine did not start through UEFI, so it has no Secure Boot']
        problems = [
            f"/usr/bin/{tool} is missing: Secure Boot here needs Limine with CachyOS's tooling"
            for tool in TOOLS
            if not self.path(f'/usr/bin/{tool}').is_file()
        ]
        if problems:
            return problems
        version = re.match(r'\s*Limine (\d+)\.', self.run('limine', '--version'))
        if not version or int(version[1]) < 12:
            problems.append('Limine is older than 12, whose Secure Boot policy this tool follows')
        problems += [
            f'no hook in /etc/boot/hooks/{kind}.d/ runs {target}, so a regeneration would not enrol and sign the loader'
            for kind, target in BOOT_HOOKS
            if not self.boot_hook(kind, target)
        ]
        settings = self.limine_settings()
        esp = settings.get('ESP_PATH') or next(
            (c for c in ('/efi', '/boot', '/boot/efi', '/limine') if (self.mount(c) or {}).get('fstype') == 'vfat'), ''
        )
        if esp.rstrip('/') != ESP or (self.mount(ESP) or {}).get('fstype') != 'vfat':
            problems.append(f'the EFI system partition is not mounted at {ESP} (ESP_PATH {esp or "not found"})')
        problems += [f'{name} is missing' for name in (CONFIG, LOADER) if not self.path(name).is_file()]
        problems += [
            f"{name} exists, and Limine reads it instead of {CONFIG}: remove it, as CachyOS's limine-install advises"
            for name in SHADOWING
            if self.path(name).is_file()
        ]
        if settings.get('ENABLE_UKI') == 'yes':
            problems.append('ENABLE_UKI=yes: unified kernel images are not the supported layout')
        if 'systemd' not in self.initramfs_hooks():
            problems.append("mkinitcpio's HOOKS lack systemd: the supported baseline has a systemd-based initramfs")
        root = self.mount('/') or {}
        if root.get('fstype') == 'overlay' or '/.snapshots/' in root.get('fsroot', ''):
            problems.append('this boot runs from a snapshot: start the current system first')
        if self.path('/var/lib/pacman/db.lck').exists():
            problems.append('a package transaction is running (/var/lib/pacman/db.lck)')
        return problems

    def require_baseline(self):
        problems = self.baseline()
        if not self.path('/usr/bin/sbctl').is_file():
            problems.append('sbctl is not installed: add it to packages.extra in the supplement and apply')
        keydir = re.search(r'(?m)^\s*keydir:\s*["\']?([^"\'\s#]+)', self.read(SBCTL_CONFIG))
        if keydir and keydir[1].rstrip('/') != KEYS:
            problems.append(f'{SBCTL_CONFIG} keeps the keys in {keydir[1]}, while this tool expects them in {KEYS}')
        if problems:
            raise RuntimeError(
                'this machine is not the supported baseline, so nothing was changed:\n'
                + ''.join(f'  - {problem}\n' for problem in problems)
            )

    # What Secure Boot looks like now.

    def flag(self, name):
        """A firmware flag such as SecureBoot or SetupMode: its byte after four of attributes."""
        path = self.path(f'{EFIVARS}/{name}-{EFI_GLOBAL}')
        data = path.read_bytes() if path.is_file() else b''
        return len(data) > 4 and data[4] == 1

    def sbctl_installed(self):
        """Whether sbctl has keys, as the boot hooks ask it before they sign."""
        return bool(json.loads(self.run('sbctl', 'status', '--json')).get('installed'))

    def own_certificates(self):
        """This machine's public certificates (DER) by firmware database, or None without keys. The
        private keys are never read."""
        found = {}
        for database, name in (('PK', 'PK'), ('KEK', 'KEK'), ('DB', 'db')):
            pem = self.path(f'{KEYS}/{name}/{name}.pem')
            if not pem.is_file():
                return None
            found[database] = ssl.PEM_cert_to_DER_cert(pem.read_text())
        return found

    def unsafe_keys(self):
        """Private keys another user could read; from their metadata only."""
        unsafe = []
        for name in ('PK', 'KEK', 'db'):
            info = self.path(f'{KEYS}/{name}/{name}.key').lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                unsafe.append(f'{KEYS}/{name}/{name}.key')
        return unsafe

    def firmware_keys(self):
        """{'PK', 'KEK', 'DB': [(DER, common name)]}: what the firmware holds."""
        listed = json.loads(self.run('sbctl', 'list-enrolled-keys', '--json')) or {}
        return {
            db: [
                (base64.b64decode(e['Raw']), (e.get('Subject') or {}).get('CommonName') or '')
                for e in listed.get(db) or []
            ]
            for db in ('PK', 'KEK', 'DB')
        }

    def holds_ours(self, keys, own):
        """Whether the firmware's platform key is this machine's and its KEK and db include this machine's."""
        return (
            own is not None
            and [der for der, _ in keys['PK']] == [own['PK']]
            and all(own[db] in [der for der, _ in keys[db]] for db in ('KEK', 'DB'))
        )

    def signed(self, name):
        """Whether sbctl finds a file signed with this machine's db key."""
        found = json.loads(self.run('sbctl', 'verify', '--json', name) or 'null') or []
        return any(item.get('file_name') == name and item.get('is_signed') == 1 for item in found)

    def database(self):
        """The files sbctl signs again after each package transaction (sbctl sign -s)."""
        return json.loads(self.run('sbctl', 'list-files', '--json') or 'null') or []

    def digest(self, path):
        """A file's BLAKE2B, as Limine checks it; each file is read once while it stays the same."""
        info = path.stat()
        key = (path, info.st_mtime_ns, info.st_size)
        if key not in self.digests:
            with open(path, 'rb') as handle:
                self.digests[key] = hashlib.file_digest(handle, 'blake2b').hexdigest()
        return self.digests[key]

    def esp_file(self, relative):
        """A file on the EFI system partition, or None when the name leads out of it."""
        esp = self.path(ESP).resolve()
        target = (esp / relative).resolve()
        return target if target.is_relative_to(esp) else None

    def hashes(self, text):
        """What Limine finds checking the files limine.conf names: {'files': host paths on the ESP it
        checks by hash, 'hashed': values that match, 'missing': values without a hash, 'wrong': values
        whose file does not match, 'cosmetic': wallpapers and fonts without a matching hash, which Limine
        skips, 'elsewhere': values not on the ESP, whose hash is not checked}."""
        found = {'files': [], 'hashed': [], 'missing': [], 'wrong': [], 'cosmetic': [], 'elsewhere': []}
        for key, value, protocol in boot_files(text):
            if protocol in CHAINLOAD and key in ('path', 'image_path'):
                continue  # the firmware checks its signature instead
            match = RESOURCE.fullmatch(value)
            if not match:
                # Another volume, which this tool cannot read; Limine still requires the hash.
                found['elsewhere' if key in COSMETIC or HASHED.fullmatch(value) else 'missing'].append(value)
                continue
            target = self.esp_file(match[1])
            exists = target is not None and target.is_file()
            if exists and key not in COSMETIC:
                found['files'].append(self.host(target))
            ok = exists and HASH.fullmatch(match[2] or '') and self.digest(target) == match[2].lower()
            if ok:
                found['hashed'].append(value)
            elif key in COSMETIC:
                found['cosmetic'].append(value)
            else:
                found['missing' if match[2] is None else 'wrong'].append(value)
        return found

    def resigned(self, files):
        """Those of `files` that sbctl signs again after each package transaction (sbctl sign -s), which
        changes them after Limine's entry tool hashed them."""
        listed = {entry.get(key) for entry in self.database() for key in ('file', 'output_file')}
        return sorted(listed & set(files))

    def with_cosmetic_hashes(self, text):
        """limine.conf with the current hash on each wallpaper and font on the ESP, which Limine only shows
        under Secure Boot with a hash that matches."""

        def fix(match):
            target = self.esp_file(match['file'])
            if target is None or not target.is_file():
                return match[0]
            return f'{match["head"]}{match["file"]}#{self.digest(target)}'

        return COSMETIC_LINE.sub(fix, text)

    def kernel_hook(self):
        """'ours', 'inert' (another override that does nothing), 'absent' or 'other'."""
        path = self.path(KERNEL_HOOK)
        if not os.path.lexists(path):
            return 'absent'
        if path.is_symlink() or not path.is_file():
            return 'other'
        text = path.read_text(errors='replace')
        return 'ours' if text == KERNEL_HOOK_TEXT else 'inert' if inert(text) else 'other'

    def boot_problems(self):
        """What keeps the boot chain from passing Limine's Secure Boot checks, once keys exist."""
        problems = []
        if not self.signed(LOADER):
            problems.append(f"{LOADER} is not signed with this machine's db key")
        settings = self.limine_settings()
        if any(settings.get(key) != value for key, value in SETTINGS.items()):
            problems.append(f'{DEFAULTS} does not set {" ".join(f"{k}={v}" for k, v in SETTINGS.items())}')
        conf = self.path(CONFIG).read_bytes()
        if hashlib.blake2b(conf).hexdigest().encode() not in self.path(LOADER).read_bytes():
            problems.append(f'the loader does not hold the checksum of {CONFIG}: Limine would refuse to start')
        found = self.hashes(conf.decode(errors='replace'))
        problems += [f'{CONFIG} names {value} without a hash: Limine would refuse it' for value in found['missing']]
        return problems + self.hash_problems(found)

    def hash_problems(self, found):
        """Hashes that fail, or will once sbctl signs their file again; a failing hash stops the boot."""
        return [f'{CONFIG} names {value}, whose file does not match its hash' for value in found['wrong']] + [
            f'sbctl signs {name} again after each package transaction, which breaks its hash: remove it from '
            f'its list with sbctl remove-file {name}'
            for name in self.resigned(found['files'])
        ]

    # What it changes.

    def change(self, what):
        self.changes.append(what)
        print(what, flush=True)

    def keep(self, **changes):
        """Record a step's result, or before an enrolment that it began."""
        self.record = {key: value for key, value in {**self.record, **changes}.items() if value is not None}
        self.make_state()
        write(self.record_path, json.dumps(self.record, indent=2, sort_keys=True) + '\n', 0o600)

    def make_state(self):
        self.state.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state, 0o700)  # the copies include limine.conf and the loader from the root-only ESP

    def copy(self, name):
        """Keep a copy of a file from before this run changed it."""
        source = self.path(name)
        if self.copies is None:
            self.make_state()
            self.copies = self.state / datetime.now(UTC).strftime('before-%Y%m%dT%H%M%S%fZ')
            self.copies.mkdir(mode=0o700)
        target = self.copies / name.lstrip('/')
        if source.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())  # efivarfs files do not take copy2's metadata
            os.chmod(target, 0o600)

    @contextmanager
    def boot_lock(self):
        path = self.path(LOCK)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def prepare(self):
        """Keys, the Limine settings, the kernel hook and signed boot files; return whether anything changed."""
        self.require_baseline()
        own = self.own_certificates()
        enrolled = self.holds_ours(self.firmware_keys(), own)
        if not self.flag('SetupMode') and not enrolled:
            raise RuntimeError(
                'the firmware is not in Setup Mode, so nothing was changed. Start its setup '
                '(systemctl reboot --firmware-setup), put Secure Boot in Setup Mode as its menu names it, start '
                'CachyOS and run --prepare again (docs/MACHINE-SETUP.md#secure-boot)'
            )
        defaults = self.read(DEFAULTS)
        wanted = with_block(defaults, DEFAULTS, SETTINGS, ''.join(f'{k}={v}\n' for k, v in SETTINGS.items()))
        hook = self.kernel_hook()
        if hook == 'other':
            raise RuntimeError(
                f"{KERNEL_HOOK} exists and does more than exit, so nothing was changed. It replaces sbctl's kernel "
                'hook; review it, delete it if it is not needed, and run --prepare again'
            )
        resigned = self.resigned(self.hashes(self.path(CONFIG).read_text())['files'])
        if resigned:
            raise RuntimeError(
                f'sbctl signs {", ".join(resigned)} again after each package transaction, which would break the '
                'hash Limine checks, so nothing was changed: remove each with sbctl remove-file and run --prepare again'
            )
        fwupd = self.path(FWUPD_EFI).is_file()
        if fwupd:
            config = self.path(FWUPD_CONFIG)
            fwupd_text = config.read_text() if config.exists() else ''
            fwupd_wanted = with_block(
                fwupd_text,
                FWUPD_CONFIG,
                FWUPD_SETTINGS,
                '[uefi_capsule]\nDisableShimForSecureBoot=true\n',
                section='uefi_capsule',
            )

        # Before the keys, so that no rebuild signs a kernel image from the moment they exist.
        if hook == 'absent':
            self.path(KERNEL_HOOK).parent.mkdir(parents=True, exist_ok=True)
            write(self.path(KERNEL_HOOK), KERNEL_HOOK_TEXT, 0o755)
            self.change(f'installed {KERNEL_HOOK}, which keeps sbctl from signing kernel images')
        if not self.sbctl_installed():
            print(self.run('sbctl', 'create-keys').strip(), flush=True)
            self.change(f'created the keys in {KEYS}')
        unsafe = self.unsafe_keys()
        if unsafe:
            raise RuntimeError(f'private keys another user can read: {", ".join(unsafe)}; make them root-only (0400)')
        if wanted != defaults:
            self.copy(DEFAULTS)
            write(self.path(DEFAULTS), wanted)
            self.change(f'turned on configuration enrolment and file verification in {DEFAULTS}')
        with self.boot_lock():
            text = self.path(CONFIG).read_text()
            hashed = self.with_cosmetic_hashes(text)
            if hashed != text:
                checked = hashlib.blake2b(text.encode()).hexdigest().encode() in self.path(LOADER).read_bytes()
                self.copy(CONFIG)
                self.copy(LOADER)
                write(self.path(CONFIG), hashed)
                self.change(f"hashed the boot menu's wallpaper in {CONFIG}")
                if checked:
                    # The loader checks limine.conf against its enrolled checksum: enrol the new one at once,
                    # as /etc/limine-entry-tool.conf asks after every edit, rather than after the rebuild.
                    self.run('limine-enroll-config')
        if self.changes or self.boot_problems():
            self.copy(CONFIG)
            self.copy(LOADER)
            print('Rebuilding the boot images with limine-mkinitcpio; its hooks enrol and sign the loader.', flush=True)
            self.run('limine-mkinitcpio')
            problems = self.boot_problems()
            if problems:
                raise RuntimeError(
                    'after limine-mkinitcpio: '
                    + '; '.join(problems)
                    + '. Solve this and run --prepare again; until --check reports no problems, '
                    + ('do not start the machine with Secure Boot on' if enrolled else 'do not enrol the keys')
                )
            self.change(f'enrolled {CONFIG} in the loader and signed it')
        if fwupd:
            self.sign_fwupd(fwupd_text, fwupd_wanted)
        if self.changes or 'prepared' not in self.record:
            self.keep(prepared={'time': datetime.now(UTC).isoformat(timespec='seconds'), 'changes': self.changes})
        if not enrolled:
            self.notes.append('next: --enroll, with the firmware still in Setup Mode')
        return bool(self.changes)

    def sign_fwupd(self, text, wanted):
        """Sign fwupd's EFI program and register it with sbctl, which signs it again after each fwupd update."""
        registered = any(e.get('file') == FWUPD_EFI and e.get('output_file') == FWUPD_SIGNED for e in self.database())
        if not (registered and self.path(FWUPD_SIGNED).is_file() and self.signed(FWUPD_SIGNED)):
            self.copy(FWUPD_SIGNED)
            self.run('sbctl', 'sign', '-s', '-o', FWUPD_SIGNED, FWUPD_EFI)
            self.change(f'signed {FWUPD_SIGNED} and registered it with sbctl')
        if wanted != text:
            self.copy(FWUPD_CONFIG)
            write(self.path(FWUPD_CONFIG), wanted, 0o640 if not self.path(FWUPD_CONFIG).exists() else None)
            self.change(f'set DisableShimForSecureBoot=true in {FWUPD_CONFIG}: CachyOS starts fwupd without shim')

    def enroll(self, with_microsoft):
        """Enrol this machine's keys, and Microsoft's unless told not to; return whether the firmware changed."""
        self.require_baseline()
        own = self.own_certificates()
        if own is None or not self.sbctl_installed():
            raise RuntimeError('there are no keys to enrol yet: run --prepare first')
        hook = self.kernel_hook()
        problems = self.boot_problems() + ([f'{KERNEL_HOOK} is missing'] if hook == 'absent' else [])
        if problems:
            raise RuntimeError(
                'run --prepare first, so that the firmware will start what it then trusts: ' + '; '.join(problems)
            )
        keys = self.firmware_keys()
        ours = self.holds_ours(keys, own)
        if ours and microsoft(keys) == with_microsoft:
            if 'enrolling' in self.record:  # an enrolment that finished but was not recorded
                self.keep(enrolled=self.enrolment(keys, with_microsoft), enrolling=None)
            self.notes.append('the firmware already holds these keys; nothing was enrolled')
            return False
        if not self.flag('SetupMode'):
            reason = (
                f"it holds these keys {'with' if microsoft(keys) else 'without'} Microsoft's, and changing that"
                if ours
                else 'enrolling'
            )
            raise RuntimeError(
                f'the firmware is not in Setup Mode, and {reason} needs it. Put Secure Boot in Setup Mode in the '
                "firmware's menu and run --enroll again; this tool never deletes keys to get there"
            )
        if not with_microsoft:
            if self.windows():
                raise RuntimeError(
                    "Windows's boot manager is signed by Microsoft only, and without Microsoft's keys the firmware "
                    'would refuse it: enrol with them (--microsoft)'
                )
            print(f'warning: {NO_MICROSOFT}', file=sys.stderr, flush=True)
        replaced = sorted(
            {
                name
                for db in keys
                for der, name in keys[db]
                if der not in own.values() and not re.search('Microsoft|Windows', name)
            }
        )
        if replaced:
            print(
                "warning: enrolment replaces the firmware's key lists, so it will no longer trust "
                f'{", ".join(replaced)}; the old lists are copied, not kept in the firmware',
                file=sys.stderr,
                flush=True,
            )
        if self.tpm_unlock():
            print(f'warning: {TPM_UNLOCK}', file=sys.stderr, flush=True)
        for path in sorted(self.path(EFIVARS).iterdir()):
            if path.name.split('-', 1)[0] in TRUST_VARIABLES:
                self.copy(self.host(path))
        self.keep(enrolling={'time': datetime.now(UTC).isoformat(timespec='seconds'), 'microsoft': with_microsoft})
        try:
            print(self.run('sbctl', 'enroll-keys', *(['--microsoft'] if with_microsoft else [])).strip(), flush=True)
        except RuntimeError:
            if self.firmware_keys() == keys:
                self.keep(enrolling=None)  # sbctl refused before it wrote anything
            raise
        keys = self.firmware_keys()
        if not self.holds_ours(keys, own) or microsoft(keys) != with_microsoft:
            raise RuntimeError(
                "sbctl enroll-keys finished, but the firmware does not list this machine's keys"
                + (" with Microsoft's" if with_microsoft else '')
                + ': read sbctl list-enrolled-keys before rebooting'
            )
        self.keep(enrolled=self.enrolment(keys, with_microsoft), enrolling=None)
        self.notes.append(
            "next: start the firmware's setup (systemctl reboot --firmware-setup), turn Secure Boot on, start "
            'CachyOS and run --check'
        )
        return True

    def enrolment(self, keys, with_microsoft):
        """The record of an enrolment: when, and what the firmware holds since."""
        return {
            'time': datetime.now(UTC).isoformat(timespec='seconds'),
            'microsoft': with_microsoft,
            'subjects': {db: [name for _, name in keys[db]] for db in keys},
        }

    # The report.

    def check(self):
        """(lines, problems): Secure Boot as the firmware, sbctl, Limine and the kernel see it. A problem
        is what stops the machine from starting, or from starting under Secure Boot once it is on."""
        if not self.path('/sys/firmware/efi').is_dir():
            return ['Secure Boot: none, the machine did not start through UEFI'], []
        enabled, setup = self.flag('SecureBoot'), self.flag('SetupMode')
        lines = [f'Secure Boot: {"on" if enabled else "off"}; Setup Mode: {"on" if setup else "off"}']
        baseline = self.baseline()
        problems = [f'not the supported baseline: {problem}' for problem in baseline]
        own = None
        if not self.path('/usr/bin/sbctl').is_file():
            lines.append('Keys: sbctl is not installed (packages.extra in the supplement)')
        else:
            own = self.own_certificates() if self.sbctl_installed() else None
            unsafe = self.unsafe_keys() if own else []
            problems += [f'{name} can be read by other users than root' for name in unsafe]
            lines.append(f'Keys: in {KEYS}' if own else 'Keys: none yet; --prepare creates them')
            keys = self.firmware_keys()
            ours = self.holds_ours(keys, own)
            others = sorted({name for db in keys for der, name in keys[db] if not own or der not in own.values()})
            if not any(keys.values()):
                lines.append('Firmware keys: none')
            else:
                lines.append(
                    f"Firmware keys: {'this machine' if ours else 'not this machine'}'s"
                    + (f'; also {", ".join(others)}' if others else '')
                )
            if ours and not microsoft(keys) and self.windows():
                problems.append("the firmware lacks Microsoft's keys, so under Secure Boot it refuses Windows")
            if enabled and not ours:
                self.notes.append(
                    "Secure Boot is on with keys that are not this machine's, which this tool did not enrol"
                )
        if self.path(CONFIG).is_file() and not baseline:
            found = self.hashes(self.path(CONFIG).read_text(errors='replace'))
            lines.append(f'Boot files: {len(found["hashed"])} hashed and matching')
            if own:
                boot = self.boot_problems()
                problems += boot
                lines.append('Loader: signed, with limine.conf enrolled' if not boot else 'Loader: see the problems')
            else:
                problems += self.hash_problems(found)
            self.notes += [
                f'{value} has no matching hash, so Limine skips it under Secure Boot' for value in found['cosmetic']
            ]
            self.notes += [
                f'{value} is not on the EFI system partition, so its hash is not checked'
                for value in found['elsewhere']
            ]
        try:
            text = self.read(DEFAULTS)
            managed = split_block(text, DEFAULTS)[1] is not None
            settings = self.limine_settings()
            lines.append(
                'Limine settings: '
                + ' '.join(f'{key}={settings.get(key, "unset")}' for key in SETTINGS)
                + (" (Shaffy OS's lines)" if managed else '')
            )
        except RuntimeError as error:
            problems.append(str(error))
        lines.append(
            'Kernel images: '
            + {
                'ours': f"left unsigned ({KERNEL_HOOK}, Shaffy OS's)",
                'inert': f'left unsigned ({KERNEL_HOOK}, not written by Shaffy OS)',
                'absent': "signed in place by sbctl's mkinitcpio hook; --prepare leaves them unsigned",
                'other': f"{KERNEL_HOOK} replaces sbctl's hook with something else; review it",
            }[self.kernel_hook()]
        )
        if self.path(FWUPD_EFI).is_file():
            signed = bool(own) and self.path(FWUPD_SIGNED).is_file() and self.signed(FWUPD_SIGNED)
            shim = ('DisableShimForSecureBoot', 'true') not in assignments(self.read(FWUPD_CONFIG), 'uefi_capsule')
            lines.append(
                f'fwupd: its EFI program {"signed" if signed else "not signed"}; {"with" if shim else "without"} shim'
            )
            if not signed or shim:
                self.notes.append('fwupd cannot install firmware capsules under Secure Boot yet; --prepare sets it up')
        if own and self.unsigned_fallback():
            self.notes.append(
                f"the firmware's fallback loader, {FALLBACK}, is Limine without your signature: should the firmware "
                'lose its entry for Limine, it cannot start the machine until Secure Boot is turned off'
            )
        if self.tpm_unlock():
            self.notes.append(TPM_UNLOCK)
        mode = re.search(r'\[(\w+)\]', self.read('/sys/kernel/security/lockdown'))
        lockdown = mode[1] if mode else 'unknown'
        lines.append(f'Kernel lockdown: {lockdown}')
        device = self.read('/sys/power/resume').strip() or '0:0'
        if device == '0:0':
            lines.append('Hibernation: this boot has no resume device (power.hibernation)')
        elif lockdown not in ('none', 'unknown') or '[disabled]' in self.read('/sys/power/disk'):
            problems.append(f'kernel lockdown ({lockdown}) turns hibernation off, so nothing can resume')
        else:
            offset = self.read('/sys/power/resume_offset').strip()
            lines.append(f'Hibernation: resumes from {device} at offset {offset}; lockdown {lockdown}')
        lines += [
            f'Recorded: {step} {self.record[step]["time"]}' for step in ('prepared', 'enrolled') if step in self.record
        ]
        if 'enrolling' in self.record:
            problems.append(f'an enrolment began at {self.record["enrolling"]["time"]} and was not recorded as done')
        return lines, problems

    def read(self, name):
        path = self.path(name)
        return path.read_text() if path.exists() else ''

    def tpm_unlock(self):
        return any('tpm2-device' in self.read(name) for name in CRYPTTAB)

    def unsigned_fallback(self):
        """Whether the firmware's fallback loader is Limine without this machine's signature."""
        path = self.path(FALLBACK)
        return path.is_file() and b'limine' in path.read_bytes().lower() and not self.signed(FALLBACK)

    def windows(self):
        """Whether Windows starts from this machine: its boot manager on the ESP, or an entry for it."""
        return self.path(WINDOWS).is_file() or 'bootmgfw.efi' in self.read(CONFIG).lower()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='report and change nothing; the default')
    mode.add_argument('--prepare', action='store_true', help='create the keys and sign the boot chain')
    mode.add_argument('--enroll', action='store_true', help='enrol the keys in the firmware')
    parser.add_argument(
        '--microsoft',
        action=argparse.BooleanOptionalAction,
        help="with --enroll: also enrol Microsoft's keys, for Windows and option ROMs; the default",
    )
    args = parser.parse_args(argv)
    if args.microsoft is not None and not args.enroll:
        parser.error('--microsoft and --no-microsoft go with --enroll')
    if os.geteuid() != 0:
        parser.error('run it as root: sudo /usr/bin/python3 scripts/configure-secure-boot.py')
    secure_boot = SecureBoot(Path('/'), run, STATE)
    status = 0
    if args.prepare:
        changed = secure_boot.prepare()
    elif args.enroll:
        changed = secure_boot.enroll(args.microsoft is not False)
    else:
        lines, problems = secure_boot.check()
        print('\n'.join(lines))
        for problem in problems:
            print(f'problem: {problem}')
        status = 1 if problems else 0
    for note in secure_boot.notes:
        print(f'note: {note}')
    if args.prepare or args.enroll:
        print(f'changed: {int(changed)}')
    return status


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as error:
        sys.exit(f'configure-secure-boot: {error}')
