#!/bin/bash
# Install CachyOS into the kit's VM without questions, from the live session of the pinned
# installer image: cachyos-installer --config vm-install.json erases /dev/vda, installs Btrfs
# with the default subvolumes, Limine with snapper, and the Niri desktop, and then runs
# vm-post-install.sh. Run it as root from the mounted share (tests/vm/README.md). On success
# the machine restarts, which ends QEMU because run-vm.sh install passes -no-reboot.
#
# --at-boot: run-vm.sh --headless install boots the image with this as systemd's only job
# (systemd.run=). The live system's own services are then started here first, and the
# installer's log is kept in the evidence share.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
at_boot=0
[[ ${1:-} != --at-boot ]] || at_boot=1
refuse() {
  printf 'install-headless: %s\n' "$*" >&2
  exit 2
}
say() { printf 'install-headless: %s\n' "$*"; }

((EUID == 0)) || refuse 'run it as root: sudo bash /run/shaffy-share/install-headless.sh'
case $(systemd-detect-virt --vm 2>/dev/null) in
  kvm | qemu) ;;
  *) refuse 'this is not a QEMU or KVM guest, and the install erases /dev/vda' ;;
esac
[[ -d /run/archiso ]] || refuse 'this is not the installer'"'"'s live session'
[[ -b /dev/vda ]] || refuse 'there is no /dev/vda to install to'
disks=$(lsblk -dn -o NAME,TYPE | awk '$2 == "disk" && $1 !~ /^(loop|sr|zram)/ {print $1}')
[[ ${disks} == vda ]] || refuse "expected one disk, vda; found: ${disks//$'\n'/ }"
command -v cachyos-installer >/dev/null || refuse 'cachyos-installer is missing from this image'
[[ ${here} == /run/shaffy-share ]] || refuse "mount the share at /run/shaffy-share (vm-install.json names its post_install step there); it is at ${here}"

keep_log() { :; }
if ((at_boot)); then
  # Booted into this job alone: start what the live session starts (networking, pacman's
  # keyring), without its desktop, and wait for the network the installer needs.
  say 'starting the live system without its desktop'
  systemctl start multi-user.target || say 'warning: multi-user.target did not start completely'
  nm-online -s -q -t 180 || say 'warning: NetworkManager reports no connection yet'
  mkdir -p /run/shaffy-evidence
  mount -t 9p -o trans=virtio,version=9p2000.L shaffy-evidence /run/shaffy-evidence
  keep_log() {
    cp /tmp/cachyos-install.log "/run/shaffy-evidence/install-$(date -u +%Y%m%dT%H%M%SZ).log" 2>/dev/null || true
    sync
  }
  diagnose() { # after a failed attempt: why the new system's own database sync fails, if it does
    [[ -f /mnt/etc/pacman.conf ]] || return 0
    {
      date -u
      pacman --sysroot /mnt -Sy --debug 2>&1 | tail -n 80
      echo '--- the new system keyring on the CachyOS signing key'
      gpg --homedir /mnt/etc/pacman.d/gnupg --list-keys --with-colons F3B607488DB35A47 2>&1
      echo '--- the cachyos database and signature the new system holds'
      ls -la /mnt/var/lib/pacman/sync/ 2>&1
      gpg --homedir /mnt/etc/pacman.d/gnupg --verify /mnt/var/lib/pacman/sync/cachyos.db.sig \
        /mnt/var/lib/pacman/sync/cachyos.db 2>&1
      echo '--- the first mirrors'
      grep -m3 '^Server' /mnt/etc/pacman.d/cachyos-mirrorlist 2>&1
    } >"/run/shaffy-evidence/diagnose-$(date -u +%Y%m%dT%H%M%SZ).log" 2>&1 || true
    sync
  }
fi
((at_boot)) || diagnose() { :; }

# The installer ranks the mirrors in this live session and installs from the fastest, but the
# new system gets the mirror lists' default order, so its first `pacman -Sy` (chwd, in the
# base install) asks another mirror. pacman replaces a database's signature even when that
# mirror answers that the database is unchanged, so a mirror behind the fastest pairs the newer
# database with its older signature, and the install fails ("signature from CachyOS is
# invalid") until that mirror catches up. So this session takes the default lists the new
# system will have, and the ranking is skipped: cachyos-rate-mirrors sources its .env file.
say 'using the default mirror lists the new system gets, without the installer'"'"'s ranking'
lists=(cachyos-mirrorlist cachyos-v3-mirrorlist cachyos-v4-mirrorlist)
pacman -Syw --noconfirm "${lists[@]}" >/dev/null
cache=$(pacman-conf CacheDir | head -n 1)
for list in "${lists[@]}"; do
  bsdtar -xOf "${cache%/}/$(pacman -Sp --print-format %f "${list}")" "etc/pacman.d/${list}" >"/etc/pacman.d/${list}"
  say "${list} starts with $(grep -m 1 '^Server' "/etc/pacman.d/${list}")"
done
mkdir -p /etc/conf.d
printf '%s\n' '# install-headless.sh (Shaffy OS VM kit): keep the default mirror lists' 'exit 0' \
  >/etc/conf.d/cachyos-rate-mirrors.env

say "$(cachyos-installer --version); installing to /dev/vda from ${here}/vm-install.json"
attempts=1
((at_boot)) && attempts=3 # unattended: a mirror caught mid-sync fails a database sync at random
for ((attempt = 1; ; attempt++)); do
  if cachyos-installer --config "${here}/vm-install.json"; then
    installed=1
    break
  fi
  installed=0
  keep_log
  diagnose
  ((attempt < attempts)) || break
  say "attempt ${attempt} failed; trying again from the start in two minutes"
  sleep 120
done
if ((installed)); then
  keep_log
  say 'done; restarting, which closes QEMU. Then run tests/vm/run-vm.sh run on the host.'
  sleep 5
  systemctl reboot
  exit 0
fi
keep_log
echo 'install-headless: the install failed; the end of its log follows (all of it is in /tmp/cachyos-install.log)' >&2
tail -n 40 /tmp/cachyos-install.log >&2 || true
exit 1
