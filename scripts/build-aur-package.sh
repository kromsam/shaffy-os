#!/bin/bash
# Build an AUR package from the AUR's current recipe as the target user, never as root.
# The recipe is cloned into the user's cache, never into the checkout, and the
# repository keeps no recipes. Two steps, each ending in one JSON line:
#
#   build-aur-package.sh fetch NAME         clone the recipe; report its commit and the
#                                           repository packages it needs to build
#   build-aur-package.sh build NAME COMMIT  build the fetched recipe at COMMIT; report the
#                                           package file and its sha256
#
# Root installs the dependencies between the two steps, and later only its own copy of the
# package, after checking the digest and the name. SHAFFY_AUR_URL replaces
# https://aur.archlinux.org (for the tests).
set -euo pipefail
[[ $(id -u) != 0 ]] || {
  echo 'build-aur-package: never build as root' >&2
  exit 1
}
usage() {
  echo 'usage: build-aur-package.sh fetch NAME | build NAME COMMIT' >&2
  exit 2
}
[[ $# -ge 2 && $2 =~ ^[a-z0-9@_+][a-z0-9@._+-]*$ ]] || usage
action=$1 name=$2
build="${XDG_CACHE_HOME:-${HOME}/.cache}/shaffy/build/${name}"
recipe="${build}/recipe"
aur="${SHAFFY_AUR_URL:-https://aur.archlinux.org}"

case ${action} in
  fetch)
    [[ $# == 2 ]] || usage
    rm -rf "${recipe}" "${build}/out"
    mkdir -p "${build}"
    git clone --quiet --depth 1 "${aur}/${name}.git" "${recipe}" >&2 \
      || {
        echo "build-aur-package: cannot fetch ${name} from ${aur}" >&2
        exit 1
      }
    # The AUR answers an unknown name with an empty repository.
    [[ -f ${recipe}/PKGBUILD ]] || {
      echo "build-aur-package: ${aur} has no recipe for ${name}" >&2
      exit 1
    }
    cd "${recipe}"
    srcinfo=$(makepkg --printsrcinfo)
    grep -qxF "pkgname = ${name}" <<<"${srcinfo}" \
      || {
        echo "build-aur-package: the ${name} recipe does not build ${name}" >&2
        exit 1
      }
    commit=$(git rev-parse HEAD)
    # The packages the build needs, without version constraints; root installs them.
    /usr/bin/python3 -c '
import json, re, sys
commit, arch = sys.argv[1], sys.argv[2]
keys = {"depends", "makedepends", "checkdepends", f"depends_{arch}", f"makedepends_{arch}", f"checkdepends_{arch}"}
needed = []
for line in sys.stdin.read().splitlines():
    key, _, value = line.strip().partition(" = ")
    if key in keys:
        name = re.split(r"[<>=]", value, maxsplit=1)[0]
        if name not in needed:
            needed.append(name)
print(json.dumps({"commit": commit, "depends": needed}))
' "${commit}" "$(uname -m)" <<<"${srcinfo}"
    ;;
  build)
    [[ $# == 3 && $3 =~ ^[0-9a-f]{40}$ ]] || usage
    [[ -d ${recipe}/.git ]] || {
      echo "build-aur-package: fetch ${name} first" >&2
      exit 1
    }
    [[ $(git -C "${recipe}" rev-parse HEAD) == "$3" ]] \
      || {
        echo "build-aur-package: the fetched ${name} recipe is not at $3" >&2
        exit 1
      }
    rm -rf "${build}/out"
    mkdir -p "${build}/out" "${build}/src" "${build}/work"
    cd "${recipe}"
    export BUILDDIR="${build}/work" PKGDEST="${build}/out" SRCDEST="${build}/src"
    makepkg --force --noconfirm >&2
    package=$(makepkg --packagelist | grep -m1 "/${name}-[^/]*\.pkg\.tar\.[a-z]*$")
    [[ -f ${package} ]] || {
      echo "build-aur-package: makepkg did not produce ${name}" >&2
      exit 1
    }
    /usr/bin/python3 -c 'import hashlib, json, sys; print(json.dumps({"path": sys.argv[1], "sha256": hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest()}))' "${package}"
    ;;
  *) usage ;;
esac
