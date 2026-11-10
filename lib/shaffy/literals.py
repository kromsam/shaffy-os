"""Personal literals in the public tree, checked by the self-test.

The public tree is every tracked or untracked, not ignored file. Two kinds of rule
scan it:

- static rules for shapes of personal data (home paths, private addresses and
  names, e-mail addresses, device and key identifiers), with an explicit allowlist
  for synthetic or attributed values;
- rules taken from the machine at scan time (the account, host name, machine-id
  and Git identity), so the owner's values are caught without being written down.
  They cannot be allowlisted, because an entry would publish the value.

A supplement adds its own rules (supplement_rules): its denylist.txt and the values
of its settings that identify the owner. The self-test scans the public tree with
them, and the profile commands scan what a person is about to share. These match
private values, so whoever reports a hit names where it matched, never the text.

Licence notices are exempt, because they may name the copyright holder: lines with
SPDX-FileCopyrightText, and the copyright line of a LICENSE file.
"""

import contextlib
from dataclasses import dataclass
import fnmatch
import ipaddress
import os
from pathlib import Path
import pwd
import re
import socket
import subprocess
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Hit:
    path: str
    line: int  # 0 for the path itself
    rule: str
    text: str


def public_files(root):
    """Tracked and untracked, not ignored files."""
    listed = subprocess.run(
        ['git', '-C', str(root), 'ls-files', '-z', '-co', '--exclude-standard'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split('\0')[:-1]
    listed = sorted({p for p in listed if os.path.lexists(Path(root) / p)})
    # A link is scanned as its target text, even when it names a folder.
    return [p for p in listed if (Path(root) / p).is_symlink() or not (Path(root) / p).is_dir()]


def lines(root, path):
    """The lines of a public file; a link's target, a binary file's bytes as latin-1."""
    full = Path(root) / path
    if full.is_symlink():
        return [os.readlink(full)]
    data = full.read_bytes()
    return data.decode('latin-1' if b'\0' in data[:8192] else 'utf-8', errors='replace').splitlines()


def notice_lines(path, content):
    """Line numbers of licence notices."""
    return {
        number
        for number, line in enumerate(content, 1)
        if 'SPDX-FileCopyrightText' in line or (Path(path).name.startswith('LICENSE') and line.startswith('Copyright'))
    }


def _word(value):
    return re.compile(r'(?i)(?<![A-Za-z0-9])' + re.escape(value) + r'(?![A-Za-z0-9])')


DOCUMENTATION = [ipaddress.ip_network(n) for n in ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24')]


def _private_ip(line, previous=''):
    found = []
    for candidate in re.findall(r'(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])', line):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        # Private, shared (carrier-grade NAT, as Tailscale uses) and link-local addresses are
        # not global; loopback, multicast, reserved and documentation ranges are fine.
        if not (
            address.is_global
            or address.is_loopback
            or address.is_multicast
            or address.is_reserved
            or address in ipaddress.ip_network('0.0.0.0/8')
            or any(address in n for n in DOCUMENTATION)
        ):
            found.append(candidate)
    found += list(re.findall(r'(?i)\bf[cd][0-9a-f]{2}:[0-9a-f:]+', line))
    return found


SYNTHETIC_DOMAINS = re.compile(r'(?i)(^|\.)(example\.(com|net|org)|[a-z0-9-]+\.(example|invalid|test|localhost))$')
SYNTHETIC_HEX = re.compile(r'(?i)^((0123456789abcdef){4}|0{64}|f{64}|(ab){32})$')
SYNTHETIC_UUID = ('00000000-0000-0000-0000-000000000000', '01234567-89ab-cdef-0123-456789abcdef')


def static_rules():
    """(rule id, function returning the matched texts of one line)."""

    def pattern(regex, keep=lambda match, context: True):
        compiled = re.compile(regex)
        return lambda line, previous='': [
            m.group(0) for m in compiled.finditer(line) if keep(m, previous + '\n' + line)
        ]

    return [
        ('home-path', pattern(r'(?<![\w.}$-])/home/[A-Za-z_][A-Za-z0-9_.-]*')),  # not a repository path
        ('private-ip', _private_ip),
        (
            'email',
            pattern(
                r'[\w.+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})\b',
                keep=lambda m, context: not SYNTHETIC_DOMAINS.search(m.group(1)),
            ),
        ),
        ('private-dns', pattern(r'(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:ts\.net|lan|home\.arpa|internal)\b')),
        ('private-dns', pattern(r'(?<![/~\w.])[a-z0-9-]+(?:\.[a-z0-9-]+)+\.local\b(?!/)')),
        (
            'device-id',
            pattern(
                r'(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',
                keep=lambda m, context: m.group(0).lower() not in SYNTHETIC_UUID,
            ),
        ),
        ('device-id', pattern(r'(?i)uuid[=:\s"\']+[0-9A-F]{4}-[0-9A-F]{4}\b')),
        ('device-id', pattern(r'(?i)serial[\'"]?\s*[:=,]\s*[\'"]?[A-Z0-9]{8,}')),
        (
            'ssh-key',
            pattern(
                r'(?:ssh-(?:ed25519|rsa|dss)|ecdsa-sha2-nistp\d+|sk-ssh-ed25519@openssh\.com) '
                r'AAAA[0-9A-Za-z+/=]{16,}',
                keep=lambda m, context: 'Example' not in m.group(0),
            ),
        ),
        ('ssh-key', pattern(r'SHA256:[0-9A-Za-z+/]{43}')),
        (
            'mac',
            pattern(
                r'(?i)\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b',
                keep=lambda m, context: (
                    m.group(0) != '00:00:00:00:00:00' and not m.group(0).lower().startswith('00:00:5e:00:53:')
                ),
            ),
        ),
        # A checksum names itself on its line or the one above; a bare one may be an identifier.
        (
            'hex-id',
            pattern(
                r'(?i)(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])',
                keep=lambda m, context: (
                    not SYNTHETIC_HEX.match(m.group(0)) and not re.search(r'(?i)sha-?256|sha512|b2sums', context)
                ),
            ),
        ),
    ]


GENERIC_ACCOUNTS = {'root', 'tester', 'runner', 'user', 'build', 'builder', 'ci', 'nobody', 'ubuntu'}
GENERIC_HOSTS = {'localhost', 'archlinux', 'cachyos', 'linux', 'runner'}


def environment_rules(root):
    """Rules for this machine's own identifiers; never allowlistable."""
    rules = []
    accounts = {pwd.getpwuid(os.getuid()).pw_name} | {os.environ.get(k, '') for k in ('USER', 'LOGNAME', 'SUDO_USER')}
    for name in sorted(a for a in accounts if len(a) >= 3 and a.lower() not in GENERIC_ACCOUNTS):
        rules.append(('account', _word(name)))
    hosts = {socket.gethostname().split('.')[0]}
    with contextlib.suppress(OSError):
        hosts.add(Path('/etc/hostname').read_text().strip().split('.')[0])
    for host in sorted(
        h for h in hosts if len(h) >= 3 and h.lower() not in GENERIC_HOSTS and not re.fullmatch(r'[0-9a-f]{12,64}', h)
    ):
        rules.append(('host', _word(host)))
    try:
        machine = Path('/etc/machine-id').read_text().strip()
        if machine:
            rules.append(('machine-id', re.compile(re.escape(machine))))
    except OSError:
        pass
    for key in ('user.name', 'user.email'):
        value = subprocess.run(
            ['git', '-C', str(root), 'config', key], capture_output=True, text=True, check=False
        ).stdout.strip()
        if value and not SYNTHETIC_DOMAINS.search(value.rsplit('@', 1)[-1]):
            rules.append(('git-identity', _word(value)))
    return [
        (rule, lambda line, previous='', c=compiled: [m.group(0) for m in c.finditer(line)]) for rule, compiled in rules
    ]


# Settings whose values identify the owner; paths and generic values match public conventions.
FIELDS = (
    'supplement_id',
    'identity.name',
    'identity.email',
    'backup.host',
    'backup.repository.id',
    'backup.repository.sftp.host',
    'backup.repository.sftp.user',
    'backup.repository.sftp.host_key',
    'tailnet.login_server',
    'tailnet.address',
)


def _field(settings, dotted):
    for key in dotted.split('.'):
        settings = settings.get(key) if isinstance(settings, dict) else None
    if not isinstance(settings, str):
        return None
    if dotted.endswith('login_server'):
        return urlsplit(settings).hostname
    if dotted.endswith('host_key'):
        return max(settings.split(), key=len)  # the key body, not its type
    return settings


def supplement_rules(root, settings):
    """Rules for a supplement's private values: each line of ROOT/denylist.txt, one Python
    regular expression applied case-insensitively, and the FIELDS values of SETTINGS, its
    config.yaml as parsed, of six characters or more. Never allowlistable. Raises ValueError
    naming a denylist line that is not a valid regular expression."""
    rules = []
    denylist = Path(root) / 'denylist.txt'
    lines = denylist.read_text().splitlines() if denylist.is_file() else []
    for number, line in enumerate(lines, 1):
        if line.strip() and not line.lstrip().startswith('#'):
            try:
                compiled = re.compile(line, re.IGNORECASE)
            except re.error:
                raise ValueError(f'{denylist} line {number} is not a valid regular expression') from None
            rules.append((f'denylist.txt line {number}', compiled))
    for name in FIELDS:
        value = _field(settings, name)
        if value and len(value) >= 6:
            rules.append((f'settings field {name}', _word(value)))
    return [
        (rule, lambda line, previous='', c=compiled: [m.group(0) for m in c.finditer(line)]) for rule, compiled in rules
    ]


def scan(root, files, rules):
    """Hits of the rules in the files' paths and lines; '_' also counts as a word break."""
    hits = []
    for path in files:
        content = lines(root, path)
        skipped = notice_lines(path, content)
        for number, line in [(0, path), *enumerate(content, 1)]:
            if number in skipped:
                continue
            previous = content[number - 2] if number > 1 else ''
            for rule, find in rules:
                found = set(find(line, previous)) | set(find(line.replace('_', '-'), previous))
                hits += [Hit(path, number, rule, text) for text in sorted(found)]
    return hits


def read_allowlist(path):
    """Entries (rule, path glob, exact text or '*', reason) of a tab-separated allowlist."""
    entries = []
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if line.strip() and not line.startswith('#'):
            fields = line.split('\t')
            if len(fields) != 4 or not fields[3].strip():
                raise ValueError(f'{path}:{number}: expected rule, path, text and reason, separated by tabs')
            entries.append(tuple(fields))
    return entries


def allowed(hit, entries):
    return any(
        hit.rule == rule and fnmatch.fnmatchcase(hit.path, glob) and text in ('*', hit.text)
        for rule, glob, text, _ in entries
    )
