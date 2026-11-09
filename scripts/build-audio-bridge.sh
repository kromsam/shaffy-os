#!/bin/bash
# Build the official Wine 10+ branch of yabridge for 64-bit VST2 plugins.
set -euo pipefail
[[ $(id -u) != 0 ]] || {
  echo 'build-audio-bridge: run as the desktop user, not root' >&2
  exit 1
}
revision=ba7022df0aee1e91cde62d7f0e940d3bc43a82b0
cache="${XDG_CACHE_HOME:-${HOME}/.cache}/shaffy/build/audio-bridge"
mkdir -p "${cache}"
source_dir="${cache}/yabridge"
build_dir="${cache}/build"
destination="${HOME}/.local/share/yabridge"
if [[ ! -d "${source_dir}/.git" ]]; then
  git clone --filter=blob:none --branch new-wine10-embedding --single-branch \
    https://github.com/robbert-vdh/yabridge.git "${source_dir}"
  git -C "${source_dir}" checkout --detach "${revision}"
fi
[[ $(git -C "${source_dir}" rev-parse HEAD) == "${revision}" ]]
[[ -z $(git -C "${source_dir}" status --porcelain --untracked-files=no) ]]
export WINEPREFIX="${cache}/build-prefix"
export WINEDEBUG=-all
if [[ ! -f "${build_dir}/build.ninja" ]]; then
  meson setup "${build_dir}" "${source_dir}" --buildtype=release \
    --cross-file="${source_dir}/cross-wine.conf" --wrap-mode=nofallback \
    -Dbitbridge=false -Dclap=false -Dvst3=false -Dbuild.cpp_std=c++20
fi
ninja -C "${build_dir}" -j2
python3 - "${build_dir}" "${destination}" "${revision}" <<'PY'
import hashlib, json, os, shutil, sys
from pathlib import Path
build, destination, revision = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
assert destination.resolve() == destination
destination.mkdir(parents=True, exist_ok=True)
names = ['yabridge-host.exe', 'yabridge-host.exe.so',
         'libyabridge-vst2.so', 'libyabridge-chainloader-vst2.so']
receipt = {}
for name in names:
    source, target = build/name, destination/name
    assert source.is_file() and not target.is_symlink()
    shutil.copy2(source, target)
    target.chmod(0o755)
    receipt[name] = hashlib.sha256(target.read_bytes()).hexdigest()
(destination/'build-receipt.json').write_text(json.dumps({
    'source': 'https://github.com/robbert-vdh/yabridge',
    'revision': revision, 'formats': ['VST2'], 'hashes': receipt,
}, indent=2)+'\n')
print('Installed the pinned official VST2 compatibility build')
PY
