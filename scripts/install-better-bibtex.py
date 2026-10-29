#!/usr/bin/python3
"""Install Better BibTeX for Zotero into the managed Zotero profile, from a pinned release.

Zettlr cites from the CSL JSON library that Better BibTeX keeps exported. This installs
only while the `zotero` integration is managed, and only into a profile without Better
BibTeX: Zotero's add-on updater keeps an installed copy current, so it is left alone.
The download must match the pinned SHA-256.
"""

import hashlib
import os
from pathlib import Path
import re
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from shaffy.ledger import Ledger
from shaffy.records import RecordError
from shaffy.selection import state_dir

VERSION = '9.0.71'
SHA256 = 'bf61a1690dc8a2b7ab09be9de2c89986f4fb6d495c82407d89b102b26fbaa9d5'
URL = f'https://github.com/retorquere/zotero-better-bibtex/releases/download/v{VERSION}/zotero-better-bibtex-{VERSION}.xpi'
PROFILE = Path('.zotero/zotero/shaffy.default')
# Zotero loads a profile's add-on from extensions/<the add-on's id>.xpi.
TARGET = PROFILE / 'extensions/better-bibtex@iris-advies.com.xpi'
# The bit of extensions.autoDisableScopes for add-ons found in the profile.
PROFILE_SCOPE = 1


def zotero_managed(home):
    try:
        integrations = Ledger(state_dir(home)).read()['integrations']
    except RecordError as error:
        raise SystemExit(error) from error
    return integrations.get('zotero', {}).get('state') == 'managed'


def enables_profile_addons(user_js):
    """Whether the profile's user.js lets Zotero enable an add-on found in the profile."""
    try:
        text = user_js.read_text()
    except FileNotFoundError:
        return False
    found = re.search(r'user_pref\("extensions\.autoDisableScopes",\s*([0-9]+)\);', text)
    return bool(found) and not int(found.group(1)) & PROFILE_SCOPE


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'shaffy-os-better-bibtex-installer'})
    return urllib.request.urlopen(request, timeout=60)


def download(url, destination):
    sha256 = hashlib.sha256()
    try:
        with fetch(url) as response, destination.open('wb') as out:
            while chunk := response.read(1 << 20):
                sha256.update(chunk)
                out.write(chunk)
        if sha256.hexdigest() != SHA256:
            raise SystemExit(f'{url} does not match the pinned SHA-256; nothing was installed')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main():
    if os.geteuid() == 0:
        raise SystemExit('Run as the desktop user, without sudo.')
    home = Path.home()
    target = home / TARGET
    if not zotero_managed(home):
        print('Better BibTeX: the zotero integration is not managed here; nothing to install.')
        return
    if target.exists():
        print(f'Better BibTeX is installed in {target}; Zotero keeps it up to date.')
        return
    if not enables_profile_addons(home / PROFILE / 'user.js'):
        raise SystemExit("Zotero's profile would keep Better BibTeX disabled; run ./bin/shaffy apply --home first.")
    # Zotero reads every file in extensions/, so the download waits beside it until it is checked.
    partial = target.parent.parent / (target.name + '.partial')
    download(URL, partial)
    target.parent.mkdir(exist_ok=True)
    os.replace(partial, target)
    print(f'Installed Better BibTeX {VERSION} in {target}; Zotero loads it when it next starts.')


if __name__ == '__main__':
    main()
