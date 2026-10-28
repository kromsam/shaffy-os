#!/usr/bin/python3
"""Install the pinned upstream icon assets for the current user, without a desktop stack."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

VERSION = '26.04.5.1ubuntu'
SHA256 = '4e35b71d37aedb44302c811f63d325f4abd7699961f76f130323b361d4c6a7d7'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path, help='Official ubuntu/yaru release tarball, checked against its pinned SHA256')
    args = parser.parse_args()
    if hashlib.file_digest(args.archive.open('rb'), 'sha256').hexdigest() != SHA256:
        raise SystemExit('Yaru archive checksum mismatch')
    target = Path.home() / '.local/share/icons'
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='shaffy-yaru-') as tmp:
        with tarfile.open(args.archive) as archive:
            archive.extractall(tmp, filter='data')
        source = Path(tmp) / ('yaru-' + VERSION) / 'icons'
        generator = source / 'src/generate-index-theme.py'
        for name in ('Yaru', 'Yaru-yellow'):
            destination = target / name
            # The version marker shows an install of ours, which a newer pin replaces; a theme
            # without one is someone else's and stays.
            marker = destination / '.shaffy-version'
            if destination.exists():
                if not marker.exists():
                    raise SystemExit(f'{destination} already exists; inspect it before replacing')
                if marker.read_text().strip() == VERSION:
                    continue
            # Built beside the old copy, which stays in use until the new one is complete.
            staged = target / f'.{name}.shaffy-new'
            shutil.rmtree(staged, ignore_errors=True)
            shutil.copytree(source / name, staged, symlinks=True)
            cmd = ['/usr/bin/python3', str(generator), name, '--source-dir', str(staged), '--output-dir', str(staged), '--inherits', 'Adwaita']
            if name != 'Yaru':
                cmd += ['--inherits', 'Yaru']
            subprocess.run(cmd, check=True)
            subprocess.run(['gtk-update-icon-cache', '-f', str(staged)], check=True)
            (staged / '.shaffy-version').write_text(VERSION + '\n')
            if destination.exists():
                shutil.rmtree(destination)
            staged.rename(destination)
    print('Installed Yaru and Yaru-yellow in ' + str(target))


if __name__ == '__main__':
    main()
