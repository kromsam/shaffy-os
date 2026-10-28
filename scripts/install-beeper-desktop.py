#!/usr/bin/python3
"""Install Beeper Desktop for the current user from Beeper's stable channel.

The AppImage updates itself afterwards, so an installed copy is left alone. A
download must match the size and SHA-512 in the channel's update feed, the file
the app's own updater reads.
"""
import base64
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlencode, urlsplit
import urllib.request

import yaml

FEED = 'https://api.beeper.com/desktop/update-feed/latest-linux.yml'
DOWNLOADS = 'beeper-desktop.download.beeper.com'
ARCH = {'x86_64': 'x64'}
# Without a version in its name the updater replaces this file in place.
TARGET = Path('.local/lib/beeper/Beeper.AppImage')
ICONS = Path('.local/share/icons/hicolor')
# The download server refuses Python's default user agent.
USER_AGENT = 'shaffy-os-beeper-installer'


def manifest_url(machine):
    if machine not in ARCH:
        raise SystemExit(f'Beeper Desktop has no Linux build for {machine}')
    # The feed answers any older version with the current release.
    return FEED + '?' + urlencode({'bundleID': 'com.automattic.beeper.desktop', 'version': '0.0.0',
                                   'platform': 'linux', 'arch': ARCH[machine], 'channel': 'stable'})


def release(text):
    """(version, url, size, sha512 digest) of the AppImage the update feed names."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or not isinstance(data.get('files'), list):
        raise SystemExit('The Beeper update feed lists no files')
    entry = next((f for f in data['files'] if str(f.get('url', '')).endswith('.AppImage')), None)
    if entry is None or not isinstance(entry.get('size'), int) or not entry.get('sha512'):
        raise SystemExit('The Beeper update feed names no AppImage with a size and SHA-512')
    url = str(entry['url'])
    if urlsplit(url).scheme != 'https' or urlsplit(url).netloc != DOWNLOADS:
        raise SystemExit(f'The Beeper update feed points outside {DOWNLOADS}: {url}')
    return str(data.get('version', '?')), url, entry['size'], base64.b64decode(entry['sha512'])


def fetch(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=60)


def download(url, destination, size, digest):
    sha512 = hashlib.sha512()
    with fetch(url) as response, destination.open('wb') as out:
        while chunk := response.read(1 << 20):
            sha512.update(chunk)
            out.write(chunk)
    if destination.stat().st_size != size or sha512.digest() != digest:
        destination.unlink()
        raise SystemExit(f'{url} does not match the size and SHA-512 in its update feed')


def install_icons(appimage, icons):
    """Copy the app's hicolor icons, which its launcher names `beepertexts`."""
    with tempfile.TemporaryDirectory(prefix='shaffy-beeper-') as tmp:
        subprocess.run([str(appimage), '--appimage-extract', 'usr/share/icons/*'], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL)
        source = Path(tmp) / 'squashfs-root/usr/share/icons/hicolor'
        for icon in source.glob('*/apps/beepertexts.png'):
            destination = icons / icon.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(icon, destination)


def main():
    if os.geteuid() == 0:
        raise SystemExit('Run as the desktop user, without sudo.')
    home = Path.home()
    target, icons = home / TARGET, home / ICONS
    if target.exists():
        print(f'Beeper Desktop is installed in {target}; it updates itself.')
        return
    with fetch(manifest_url(platform.machine())) as response:
        version, url, size, digest = release(response.read())
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.partial')
    download(url, partial, size, digest)
    partial.chmod(0o755)
    install_icons(partial, icons)
    os.replace(partial, target)
    print(f'Installed Beeper Desktop {version} in {target}')


if __name__ == '__main__':
    sys.exit(main())
