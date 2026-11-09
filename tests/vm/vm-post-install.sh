#!/bin/bash
# The post_install step of vm-install.json. cachyos-installer runs it as root in the live
# session after a headless install, when it has already unmounted the new system: it mounts
# the new root and lists the kit's two shares in its /etc/fstab, so they are mounted at
# every boot. When the share carries run-vm.sh --headless's public key (authorized_keys),
# it also enables sshd and authorises that key for the synthetic account, so the host can
# drive the guest (tests/vm/drive.py). Given a directory, it uses that as the new root
# instead and changes only files (for the tests).
set -euo pipefail
share=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
account=demo
if (($#)); then
  target=$1
  live=0
else
  live=1
  # The installer's own log writer overwrites what this step appends to its log, so keep
  # a copy in the evidence share when install-headless.sh --at-boot has mounted it.
  if mountpoint -q /run/shaffy-evidence; then
    exec > >(tee -a "/run/shaffy-evidence/post-install-$(date -u +%Y%m%dT%H%M%SZ).log") 2>&1
  fi
  target=$(mktemp -d /run/shaffy-target.XXXXXX)
  mount -o subvol=@ /dev/vda2 "${target}"
  # pacman below writes its cache and log, and package hooks may rebuild the initramfs and
  # Limine's entries, so the subvolumes and the EFI partition they use are mounted too.
  mount -o subvol=@home /dev/vda2 "${target}/home"
  mount -o subvol=@cache /dev/vda2 "${target}/var/cache"
  mount -o subvol=@log /dev/vda2 "${target}/var/log"
  mount /dev/vda1 "${target}/boot"
  trap 'umount "$target/boot" "$target/var/log" "$target/var/cache" "$target/home"; umount "$target" && rmdir "$target"' EXIT
fi
fstab=${target}/etc/fstab
[[ -f ${fstab} ]] || {
  echo "vm-post-install: ${fstab} is missing; the install did not finish" >&2
  exit 1
}
if ! grep -q '^shaffy-share ' "${fstab}"; then
  cat >>"${fstab}" <<'EOF'

# The Shaffy OS VM kit's shares (tests/vm/README.md); nofail keeps the boot going without them.
shaffy-share     /mnt/shaffy-share     9p  trans=virtio,version=9p2000.L,ro,nofail  0 0
shaffy-evidence  /mnt/shaffy-evidence  9p  trans=virtio,version=9p2000.L,nofail     0 0
EOF
fi
mkdir -p "${target}/mnt/shaffy-share" "${target}/mnt/shaffy-evidence"
echo 'vm-post-install: the shares are listed in /etc/fstab'

# cachyos-installer 0.12.4 runs the base install with its own pacman.conf, which adds the
# repositories built for this CPU (cachyos-znver4 and its siblings), but gives the new system
# a copy of the live session's, which lacks them (host_files_to_copy in
# installer-lib/src/packages.cpp). Every later install and update there would then come
# from the generic repositories, and `pacman -S` would downgrade what the base installed.
# Give the new system the configuration its base install used.
installer_conf=/tmp/cachyos-installer-pacman.conf
if ((live)) && [[ -f ${installer_conf} ]] && ! cmp -s "${installer_conf}" "${target}/etc/pacman.conf"; then
  install -m 0644 "${installer_conf}" "${target}/etc/pacman.conf"
  echo "vm-post-install: pacman.conf now lists the repositories the base install used:" \
    "$(grep -o '^\[[^]]*\]' "${target}/etc/pacman.conf" | grep -v options | tr '\n' ' ')"
fi

# cachyos-installer 0.12.4 accepts "desktop": "niri" and its net profiles define
# [desktop.niri], but the table that maps a desktop to its profile (kDesktopMatches in
# gucc/src/package_list.cpp) has no niri entry, so a headless install gets no desktop.
# Install that profile's packages and enable its display manager, as the installer would.
if ((live)) && [[ ! -e ${target}/usr/share/wayland-sessions/niri.desktop ]]; then
  arch-chroot "${target}" pacman -S --needed --noconfirm cachyos-niri-noctalia sddm
  systemctl --root="${target}" enable sddm.service
  # The installer created the account before this, so its home lacks what the profile puts
  # in /etc/skel: Niri's configuration, which starts Noctalia, and Noctalia's. Copy what
  # useradd -m would have copied, as the account, keeping every file the home has already.
  skel_home=$(awk -F: -v account="${account}" '$1 == account {print $6}' "${target}/etc/passwd")
  arch-chroot -u "${account}" "${target}" cp -r --update=none /etc/skel/. "${skel_home}/"
  echo 'vm-post-install: installed the Niri desktop profile (cachyos-niri-noctalia, sddm), which the installer skipped'
fi

[[ -f ${share}/authorized_keys ]] || exit 0
IFS=: read -r _ _ uid gid _ home _ < <(grep "^${account}:" "${target}/etc/passwd") \
  || {
    echo "vm-post-install: there is no account ${account}" >&2
    exit 1
  }
install -d -m 0700 -o "${uid}" -g "${gid}" "${target}${home}/.ssh"
install -m 0600 -o "${uid}" -g "${gid}" "${share}/authorized_keys" "${target}${home}/.ssh/authorized_keys"
if ((live)); then
  if [[ ! -x ${target}/usr/bin/sshd ]]; then
    arch-chroot "${target}" pacman -S --needed --noconfirm openssh
  fi
  systemctl --root="${target}" enable sshd.service
  # CachyOS enables ufw with incoming connections denied; let only QEMU's host side in.
  arch-chroot "${target}" ufw allow from 10.0.2.2 to any port 22 proto tcp comment 'Shaffy OS VM kit: SSH from the host'
  # The kernel and a login prompt on the serial console, which run-vm.sh --headless logs.
  sed -i 's/^\(KERNEL_CMDLINE\[default\]="[^"]*\)"/\1 console=tty0 console=ttyS0,115200"/' "${target}/etc/default/limine"
  sed -i 's/^\(\s*cmdline: .*\)$/\1 console=tty0 console=ttyS0,115200/' "${target}/boot/limine.conf"
fi
echo "vm-post-install: sshd is enabled and the host's key may log in as ${account}"
