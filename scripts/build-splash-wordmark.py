#!/usr/bin/python3
"""Draw the boot splash's Shaffy OS wordmark, the watermark.png of the shaffy-os Plymouth theme.

The wordmark is SVG text rendered by rsvg-convert (librsvg) in JetBrainsMono Nerd Font, the
desktop's own font (ttf-jetbrains-mono-nerd, in the desktop package group). The PNG is kept in
the repository beside the theme file, so installing the theme needs no graphics toolchain. The
theme centres it near the bottom of the screen, where CachyOS's theme draws its logo; Plymouth
draws it at its own size over the black background.
"""

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'roles/boot_splash/files/shaffy-os/watermark.png'
WIDTH, HEIGHT = 256, 48
FONT = 'JetBrainsMono Nerd Font'


def svg():
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">'
        f'<text x="{WIDTH // 2}" y="33" text-anchor="middle" font-family="{FONT}" font-size="28" '
        'font-weight="500" letter-spacing="2" fill="#e6e6e6">Shaffy OS</text></svg>'
    )


def build(output):
    # rsvg-convert falls back to another font without a word, so check that fontconfig has this one.
    family = subprocess.run(['fc-match', '-f', '%{family}', FONT], capture_output=True, text=True, check=True).stdout
    if FONT not in family.split(','):
        raise RuntimeError(f'fontconfig has no {FONT} (it offers {family}): install ttf-jetbrains-mono-nerd')
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['rsvg-convert', '--output', str(output)], input=svg(), text=True, check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--output', type=Path, default=OUTPUT, help=f'default: {OUTPUT.relative_to(ROOT)}')
    args = parser.parse_args(argv)
    build(args.output)
    print(f'wrote {args.output}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(f'build-splash-wordmark: {error}')
