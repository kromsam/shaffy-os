#!/usr/bin/env bash
# User-level publishing dependencies; no administrator authentication needed.
set -euo pipefail
[[ $(id -u) != 0 ]] || { echo 'install-publishing-tools: run as the desktop user, not root' >&2; exit 1; }
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
pin() {  # the version home/dot_config/mise/config.toml pins for a tool
  /usr/bin/python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["tools"][sys.argv[2]])' \
    "$repo/home/dot_config/mise/config.toml" "$1"
}
mise install -y "aqua:quarto-dev/quarto-cli@$(pin aqua:quarto-dev/quarto-cli)"
downloads="${XDG_CACHE_HOME:-$HOME/.cache}/shaffy/downloads"
mkdir -p "$downloads"
archive="$downloads/qpdf-12.4.1-bin-linux-x86_64.zip"
if [[ ! -f "$archive" ]]; then
  curl --fail --location --output "$archive" \
    https://github.com/qpdf/qpdf/releases/download/v12.4.1/qpdf-12.4.1-bin-linux-x86_64.zip
fi
python3 - "$archive" <<'PY'
import hashlib, os, zipfile, sys, stat
from pathlib import Path, PurePosixPath
archive = Path(sys.argv[1])
assert hashlib.sha256(archive.read_bytes()).hexdigest() == 'db9122e88ec00c76ac6a14e09ffb92406db1773d47b968911ff6e69f28c09bf9'
target = Path.home()/'.local/share/qpdf/12.4.1'
assert target.resolve() == target
target.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(archive) as z:
    for f in z.infolist():
        p = PurePosixPath(f.filename)
        assert not p.is_absolute() and '..' not in p.parts
        out = target / f.filename
        if stat.S_ISLNK(f.external_attr >> 16):
            link = z.read(f).decode()
            assert not Path(link).is_absolute()
            assert (out.parent / link).resolve().is_relative_to(target)
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.is_symlink():
                assert str(out.readlink()) == link
            else:
                # Earlier ZIP extraction may have materialized link text.
                if out.exists():
                    assert out.read_bytes() == z.read(f)
                    out.unlink()
                out.symlink_to(link)
        else:
            assert not out.is_symlink()
            z.extract(f, target)
        if not f.is_dir() and not out.is_symlink():
            out.chmod((f.external_attr >> 16) & 0o777 or 0o644)
print('Installed checksum-verified official qpdf binaries')
PY
