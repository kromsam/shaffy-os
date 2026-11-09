#!/bin/bash
# shellcheck disable=SC2054,SC2174 # QEMU separates an option's values with commas; only the
# last directory of each mkdir -p needs 0700.
# The host half of the VM validation (tests/vm/README.md): a CachyOS guest in QEMU
# with KVM, UEFI firmware and a virtio-vga-gl display, so Niri gets a render device.
# A read-only 9p share carries a Git bundle of this checkout, the guest script and
# the synthetic supplements; a writable 9p share receives the evidence.
#
# It writes only its own folder, ~/.cache/shaffy/vm/ (the disk, a private copy of
# the firmware variables, the share and, by default, the evidence), and never
# needs root.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: tests/vm/run-vm.sh [options] install|run|share

  install   create the disk and firmware variables when missing, build the share
            when missing, and boot the pinned installer image; QEMU exits when
            the installer restarts the machine
  run       boot the installed disk
  share     rebuild the read-only share from this checkout's HEAD

Options:
  --dry-run              print what would be created and the QEMU command; change nothing
  --iso PATH --sha256 H  another installer image and its SHA-256 pin, always together
  --memory MIB           guest memory in MiB (default 4096)
  --cpus N               virtual CPUs (default: half the host's, from 2 to 4)
  --evidence DIR         the writable evidence folder (default ~/.cache/shaffy/vm/evidence)
  --dir DIR              the VM folder (default ~/.cache/shaffy/vm)
  --headless             no window: an offscreen GL display, the serial console in a log
                         file in the evidence folder, a QMP socket (VM folder, qmp.sock) for
                         keys, a VNC socket (vnc.sock) for screenshots, and SSH on
                         127.0.0.1:2222 with the VM folder's key; install then boots the
                         image's kernel with the headless install as its only job
                         (tests/vm/drive.py drives the rest)
EOF
}

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
# The installer image, pinned by path and SHA-256; --iso with --sha256 replaces both.
ISO="${HOME}/Downloads/cachyos-desktop-linux-260809.iso"
ISO_SHA256=959f6577f45e25ee9fd8c220fd221b08e4ea79412c7315c0f922dd6d86d5e33c
DISK_SIZE=40G
MEMORY=4096
VM_DIR="${XDG_CACHE_HOME:-${HOME}/.cache}/shaffy/vm"
EVIDENCE=
CPUS=
DRY_RUN=0
HEADLESS=0
SSH_PORT=2222
COMMAND=
iso_given='' sha_given=''

die() {
  printf 'run-vm: %s\n' "$*" >&2
  exit 1
}
say() { printf 'run-vm: %s\n' "$*" >&2; }

while (($#)); do
  case $1 in
    --dry-run) DRY_RUN=1 ;;
    --headless) HEADLESS=1 ;;
    --iso)
      iso_given=${2:?--iso needs a path}
      shift
      ;;
    --sha256)
      sha_given=${2:?--sha256 needs a checksum}
      shift
      ;;
    --memory)
      MEMORY=${2:?--memory needs MiB}
      shift
      ;;
    --cpus)
      CPUS=${2:?--cpus needs a number}
      shift
      ;;
    --evidence)
      EVIDENCE=${2:?--evidence needs a folder}
      shift
      ;;
    --dir)
      VM_DIR=${2:?--dir needs a folder}
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    install | run | share)
      [[ -z ${COMMAND} ]] || {
        usage >&2
        exit 2
      }
      COMMAND=$1
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
  shift
done
[[ -n ${COMMAND} ]] || {
  usage >&2
  exit 2
}
if [[ -n ${iso_given} || -n ${sha_given} ]]; then
  [[ -n ${iso_given} && -n ${sha_given} ]] || {
    printf 'run-vm: --iso and --sha256 go together\n' >&2
    exit 2
  }
  [[ ${sha_given} =~ ^[0-9a-fA-F]{64}$ ]] || {
    printf 'run-vm: --sha256 takes 64 hexadecimal digits\n' >&2
    exit 2
  }
  ISO=${iso_given}
  ISO_SHA256=${sha_given,,}
fi
# shellcheck disable=SC2015 # both are tests: the block runs when either fails
[[ ${MEMORY} =~ ^[0-9]+$ ]] && ((MEMORY >= 2048)) || {
  printf 'run-vm: --memory takes MiB, at least 2048\n' >&2
  exit 2
}
HOST_CPUS=$(nproc)
if [[ -z ${CPUS} ]]; then
  CPUS=$((HOST_CPUS / 2))
  if ((CPUS < 2)); then CPUS=2; fi
  if ((CPUS > 4)); then CPUS=4; fi
  if ((CPUS > HOST_CPUS)); then CPUS=${HOST_CPUS}; fi
fi
# shellcheck disable=SC2015 # both are tests: the block runs when either fails
[[ ${CPUS} =~ ^[0-9]+$ ]] && ((CPUS >= 1 && CPUS <= HOST_CPUS)) || {
  printf 'run-vm: --cpus takes 1 to %s\n' "${HOST_CPUS}" >&2
  exit 2
}
VM_DIR=${VM_DIR%/}
EVIDENCE=${EVIDENCE:-${VM_DIR}/evidence}
SHARE=${VM_DIR}/share
DISK=${VM_DIR}/disk.qcow2

# UEFI firmware as a code and variables pair: the Arch edk2-ovmf paths first, then other
# layouts, then QEMU's firmware descriptors. A Secure Boot or SMM build is never chosen.
FIRMWARE_PAIRS=(
  /usr/share/edk2/x64/OVMF_CODE.4m.fd /usr/share/edk2/x64/OVMF_VARS.4m.fd
  /usr/share/edk2-ovmf/x64/OVMF_CODE.fd /usr/share/edk2-ovmf/x64/OVMF_VARS.fd
  /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_VARS_4M.fd
  /usr/share/edk2/ovmf/OVMF_CODE.fd /usr/share/edk2/ovmf/OVMF_VARS.fd
)
find_firmware() {
  local i
  for ((i = 0; i < ${#FIRMWARE_PAIRS[@]}; i += 2)); do
    if [[ -r ${FIRMWARE_PAIRS[i]} && -r ${FIRMWARE_PAIRS[i + 1]} ]]; then
      FIRMWARE_CODE=${FIRMWARE_PAIRS[i]}
      FIRMWARE_VARS=${FIRMWARE_PAIRS[i + 1]}
      return 0
    fi
  done
  command -v python3 >/dev/null || return 1
  local found
  found=$(
    python3 - <<'EOF'
import glob, json
for path in sorted(glob.glob('/usr/share/qemu/firmware/*.json')):
    try:
        data = json.load(open(path))
        mapping = data['mapping']
        machines = [m for t in data['targets'] if t['architecture'] == 'x86_64' for m in t['machines']]
        if ('uefi' in data['interface-types'] and mapping.get('device') == 'flash'
                and any(m.startswith('pc-q35') for m in machines)
                and not {'requires-smm', 'secure-boot', 'enrolled-keys'} & set(data.get('features', []))):
            print(mapping['executable']['filename'])
            print(mapping['nvram-template']['filename'])
            break
    except (OSError, ValueError, KeyError, TypeError):
        continue
EOF
  )
  [[ -n ${found} ]] || return 1
  FIRMWARE_CODE=$(sed -n 1p <<<"${found}")
  FIRMWARE_VARS=$(sed -n 2p <<<"${found}")
  [[ -r ${FIRMWARE_CODE} && -r ${FIRMWARE_VARS} ]]
}

# Every missing host prerequisite at once, each with what provides it.
host_problems() {
  local problems=()
  problem() { problems+=("$*"); }
  if [[ ${COMMAND} != share ]]; then
    [[ -r /dev/kvm && -w /dev/kvm ]] || problem "KVM is not usable: /dev/kvm is missing or not writable" \
      "for you (enable virtualisation in the firmware settings and load kvm_amd or kvm_intel)"
    local tool
    for tool in qemu-system-x86_64 qemu-img; do
      command -v "${tool}" >/dev/null || problem "${tool} is missing; install QEMU with: sudo pacman -S --needed qemu-desktop"
    done
    find_firmware || problem "no UEFI firmware (OVMF) found; qemu-desktop brings it in the edk2-ovmf package:" \
      "sudo pacman -S --needed qemu-desktop edk2-ovmf"
    if [[ ${COMMAND} == install ]]; then
      [[ -r ${ISO} ]] || problem "the installer image ${ISO} is missing; download it, or name another one with" \
        "--iso PATH --sha256 SHA256"
      if ((HEADLESS)); then
        command -v bsdtar >/dev/null || problem "bsdtar is missing; it reads the kernel from the image (libarchive)"
      fi
    else
      [[ -f ${DISK} ]] || problem "there is no installed disk at ${DISK}; run tests/vm/run-vm.sh install first"
    fi
  fi
  if ((HEADLESS)) && [[ ${COMMAND} != share ]]; then
    command -v ssh-keygen >/dev/null || problem "ssh-keygen is missing (openssh)"
    [[ -r /dev/dri/renderD128 ]] || problem "no render node for the offscreen display: /dev/dri/renderD128 is missing or unreadable"
  fi
  if ! command -v git >/dev/null; then
    problem "git is missing; the share holds a Git bundle of the checkout"
  elif [[ ${COMMAND} == share || ! -f ${SHARE}/SHARE-INFO ]] &&
    ! git -C "${ROOT}" cat-file -e HEAD:tests/vm/guest-validate.sh 2>/dev/null; then
    problem "HEAD has no tests/vm/guest-validate.sh; the share is built from commits only, so commit the kit first"
  fi
  ((${#problems[@]} == 0)) || printf '%s\n' "${problems[@]}"
}

option_path() { # a path inside a QEMU option, whose commas QEMU reads doubled
  printf '%s' "${1//,/,,}"
}

show_command() { # one line a shell can run, quoting only what needs it
  local argument words=()
  for argument; do
    if [[ ${argument} =~ ^[A-Za-z0-9_./,=:@%+-]+$ ]]; then
      words+=("${argument}")
    else
      words+=("'${argument//"'"/"'\\''"}'")
    fi
  done
  printf '%s\n' "${words[*]}"
}

run_or_show() { # run a preparation step, or show it in a dry run
  if ((DRY_RUN)); then
    printf '# would run: %s\n' "$(show_command "$@")"
  else
    "$@"
  fi
}

# The share, rebuilt in place from HEAD: QEMU keeps the share folder itself open, so its
# contents are replaced and the folder stays.
build_share() {
  local head branch staging
  head=$(git -C "${ROOT}" rev-parse HEAD)
  branch=$(git -C "${ROOT}" symbolic-ref -q --short HEAD || true)
  if [[ -n $(git -C "${ROOT}" status --porcelain) ]]; then
    say "the checkout has uncommitted changes; the share holds commit ${head} without them"
  fi
  local installs=(install-headless.sh vm-install.json vm-post-install.sh)
  local sources=(tests/vm/guest-validate.sh "${installs[@]/#/tests/vm/}" supplement.example
    tests/fixtures/supplements/vm)
  if ((DRY_RUN)); then
    printf '# would build the share %s from commit %s: %s\n' "${SHARE}" "${head}" \
      'shaffy-os.bundle, guest-validate.sh, the headless install files, supplements/example and supplements/vm'
    return 0
  fi
  local source
  for source in "${sources[@]}"; do
    git -C "${ROOT}" cat-file -e "${head}:${source}" 2>/dev/null ||
      die "commit ${head} has no ${source}; the share is built from commits only, so commit the kit first"
  done
  mkdir -p -m 0700 "${VM_DIR}"
  mkdir -p "${SHARE}"
  staging=$(mktemp -d "${VM_DIR}/share-new.XXXXXX")
  local refs=(HEAD)
  [[ -z ${branch} ]] || refs+=("refs/heads/${branch}")
  git -C "${ROOT}" bundle create -q "${staging}/shaffy-os.bundle" "${refs[@]}"
  git -C "${ROOT}" archive --format=tar --prefix=tree/ "${head}" "${sources[@]}" | tar -x -C "${staging}"
  mkdir "${staging}/supplements"
  mv "${staging}/tree/tests/vm/guest-validate.sh" "${staging}/guest-validate.sh"
  local file
  for file in "${installs[@]}"; do mv "${staging}/tree/tests/vm/${file}" "${staging}/${file}"; done
  mv "${staging}/tree/supplement.example" "${staging}/supplements/example"
  mv "${staging}/tree/tests/fixtures/supplements/vm" "${staging}/supplements/vm"
  rm -rf "${staging}/tree"
  {
    printf 'commit=%s\n' "${head}"
    printf 'branch=%s\n' "${branch:-detached}"
    printf 'built=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'uncommitted_changes_left_out=%s\n' "$([[ -n $(git -C "${ROOT}" status --porcelain) ]] && echo yes || echo no)"
  } >"${staging}/SHARE-INFO"
  rm -rf "${SHARE}/supplements"
  mv "${staging}/supplements" "${SHARE}/supplements"
  mv -f "${staging}/shaffy-os.bundle" "${staging}/guest-validate.sh" "${installs[@]/#/${staging}/}" \
    "${staging}/SHARE-INFO" "${SHARE}/"
  rmdir "${staging}"
  say "the share holds commit ${head} (${branch:-detached}) at ${SHARE}"
}

found=$(host_problems)
if [[ -n ${found} ]]; then
  if ((DRY_RUN)); then
    while IFS= read -r line; do say "note: a real run would refuse: ${line}"; done <<<"${found}"
  else
    while IFS= read -r line; do say "${line}"; done <<<"${found}"
    exit 1
  fi
fi

if [[ ${COMMAND} == share ]]; then
  build_share
  exit 0
fi

find_firmware || {
  FIRMWARE_CODE=${FIRMWARE_PAIRS[0]}
  FIRMWARE_VARS=${FIRMWARE_PAIRS[1]}
}
VARS_COPY=${VM_DIR}/$(basename "${FIRMWARE_VARS}")

if [[ ${COMMAND} == install ]]; then
  if ((DRY_RUN)); then
    printf '# would check %s against its pinned SHA-256 %s\n' "${ISO}" "${ISO_SHA256}"
  else
    say "checking the SHA-256 of ${ISO}"
    actual=$(sha256sum "${ISO}" | cut -d' ' -f1)
    [[ ${actual} == "${ISO_SHA256}" ]] || die "${ISO} has SHA-256 ${actual}, not the pinned ${ISO_SHA256}"
  fi
fi

available=$(awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo)
if ((available < MEMORY + 512)); then
  say "warning: ${available} MiB of memory is available and the guest takes ${MEMORY} MiB; close applications first or pass --memory"
fi

if ((! DRY_RUN)); then mkdir -p -m 0700 "${VM_DIR}"; fi
if [[ ${COMMAND} == install && ! -f ${DISK} ]]; then
  run_or_show qemu-img create -q -f qcow2 -o nocow=on "${DISK}" "${DISK_SIZE}"
fi
[[ -f ${VARS_COPY} ]] || run_or_show install -m 0600 "${FIRMWARE_VARS}" "${VARS_COPY}"
[[ -d ${EVIDENCE} ]] || run_or_show mkdir -p -m 0700 "${EVIDENCE}"
[[ -f ${SHARE}/SHARE-INFO ]] || build_share

if ((HEADLESS)); then
  # A throwaway key for the synthetic account, which the headless install authorises.
  if [[ ! -f ${VM_DIR}/ssh/id_ed25519 ]]; then
    run_or_show mkdir -p -m 0700 "${VM_DIR}/ssh"
    run_or_show ssh-keygen -q -t ed25519 -N '' -C shaffy-vm-kit -f "${VM_DIR}/ssh/id_ed25519"
  fi
  ((DRY_RUN)) || install -m 0644 "${VM_DIR}/ssh/id_ed25519.pub" "${SHARE}/authorized_keys"
fi

qemu=(
  qemu-system-x86_64
  -name shaffy-vm
  -machine q35,accel=kvm
  -cpu host
  -smp "${CPUS}"
  -m "${MEMORY}"
  -drive "if=pflash,format=raw,unit=0,readonly=on,file=$(option_path "${FIRMWARE_CODE}")"
  -drive "if=pflash,format=raw,unit=1,file=$(option_path "${VARS_COPY}")"
  -drive "file=$(option_path "${DISK}"),if=none,id=disk,format=qcow2,discard=unmap"
  -device virtio-blk-pci,drive=disk,bootindex=1
  -device virtio-vga-gl
  -device virtio-tablet-pci
  -device virtio-rng-pci
  -virtfs "local,path=$(option_path "${SHARE}"),mount_tag=shaffy-share,security_model=none,readonly=on,id=shaffy-share"
  -virtfs "local,path=$(option_path "${EVIDENCE}"),mount_tag=shaffy-evidence,security_model=mapped-xattr,id=shaffy-evidence"
)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
if ((HEADLESS)); then
  # Niri still gets its GL device, and egl-headless copies each frame for the VNC socket,
  # which gives screenshots; QMP gives keys, SSH a shell. All of them are local only.
  qemu+=(-display egl-headless
    -serial "file:$(option_path "${EVIDENCE}/serial-${COMMAND}-${STAMP}.log")"
    -qmp "unix:$(option_path "${VM_DIR}/qmp.sock"),server=on,wait=off"
    -vnc "unix:$(option_path "${VM_DIR}/vnc.sock")"
    -pidfile "${VM_DIR}/qemu.pid"
    -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22")
else
  qemu+=(-display gtk,gl=on -nic user,model=virtio-net-pci)
fi
if ((HEADLESS)); then
  : # no sound card without a listener
elif [[ -S ${XDG_RUNTIME_DIR:-/nonexistent}/pipewire-0 ]]; then
  qemu+=(-audiodev pipewire,id=audio -device ich9-intel-hda -device hda-duplex,audiodev=audio)
else
  say "note: no PipeWire socket on the host, so the guest has no sound card"
fi
if [[ ${COMMAND} == install ]]; then
  # The installer boots first; its restart ends QEMU, and `run` boots the installed disk.
  qemu+=(-drive "file=$(option_path "${ISO}"),media=cdrom,readonly=on,if=none,id=installer" -no-reboot)
  if ((! HEADLESS)); then
    qemu+=(-device ide-cd,drive=installer,bootindex=0)
  else
    # -kernel takes boot index 0 itself; the image is then only the live system's source.
    # The image's own kernel, booted with its live parameters plus a serial console, and the
    # headless install as the one job systemd boots into; its end powers the machine off.
    # systemd.unit= names the job's target, because systemd-run-generator's default.target
    # ranks below the image's own /etc/systemd/system/default.target.
    boot=${VM_DIR}/boot
    if ((DRY_RUN)); then
      printf '# would extract the kernel, initramfs and GRUB configuration from %s into %s\n' "${ISO}" "${boot}"
      live='archisobasedir=arch archisosearchuuid=UUID-FROM-THE-IMAGE'
    else
      rm -rf "${boot}" && mkdir -p -m 0700 "${boot}"
      bsdtar -x -C "${boot}" -f "${ISO}" arch/boot/x86_64/vmlinuz-linux-cachyos \
        arch/boot/x86_64/initramfs-linux-cachyos.img boot/grub/grub.cfg
      live=$(awk '$1 == "linux" && $2 ~ /vmlinuz-linux-cachyos$/ {$1 = ""; $2 = ""; print; exit}' \
        "${boot}/boot/grub/grub.cfg" | sed 's/^ *//')
      [[ ${live} == *archisosearchuuid=* ]] || die "cannot read the live kernel parameters from the image's GRUB configuration"
    fi
    qemu+=(-device ide-cd,drive=installer)
    job="mkdir -p /run/shaffy-share && mount -t 9p -o trans=virtio,version=9p2000.L,ro shaffy-share /run/shaffy-share"
    job+=" && exec bash /run/shaffy-share/install-headless.sh --at-boot"
    qemu+=(-kernel "${VM_DIR}/boot/arch/boot/x86_64/vmlinuz-linux-cachyos"
      -initrd "${VM_DIR}/boot/arch/boot/x86_64/initramfs-linux-cachyos.img"
      -append "${live} copytoram=n console=tty0 console=ttyS0,115200 systemd.run=\"/usr/bin/bash -c '${job}'\" systemd.run_success_action=poweroff systemd.run_failure_action=poweroff systemd.unit=kernel-command-line.target")
  fi
fi

if ((DRY_RUN)); then
  show_command "${qemu[@]}"
  exit 0
fi

# What the host ran, beside the guest's evidence.
{
  printf 'date=%s\ncommand=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${COMMAND}"
  printf 'qemu=%s\n' "$(qemu-system-x86_64 --version | head -1)"
  printf 'firmware=%s %s\n' "${FIRMWARE_CODE}" "${FIRMWARE_VARS}"
  if command -v pacman >/dev/null; then printf 'packages=%s\n' "$(pacman -Q qemu-desktop edk2-ovmf 2>/dev/null | tr '\n' ' ')"; fi
  [[ ${COMMAND} != install ]] || printf 'installer=%s sha256=%s (verified)\n' "${ISO}" "${ISO_SHA256}"
  printf 'share:\n'
  sed 's/^/  /' "${SHARE}/SHARE-INFO"
  printf 'qemu command:\n  %s\n' "$(show_command "${qemu[@]}")"
} >"${EVIDENCE}/host-${COMMAND}-${STAMP}.txt"

say "starting the VM (${CPUS} vCPUs, ${MEMORY} MiB); the share holds commit $(sed -n 's/^commit=//p' "${SHARE}/SHARE-INFO")"
exec "${qemu[@]}"
