# Machine setup

Shaffy OS configures a desktop on a system that is already installed. Some
choices belong to the machine rather than the desktop: they depend on the disk,
the firmware or the hardware, or they cannot be undone by a snapshot. Shaffy OS
sets its own boot splash, sets up swap and hibernation when the supplement asks
for them, has a tool for Secure Boot that you run by hand, and leaves the others
to you. This page lists them, says how each one meets a Shaffy OS setting, and
points to the sources to follow. [Supplement
suggestions](SUPPLEMENT-SUGGESTIONS.md#machine-tools) shows how to keep your own
tooling for them in your supplement.

| Area | Who sets it up | What in Shaffy OS depends on it |
| --- | --- | --- |
| [Swap and hibernation](#swap-and-hibernation) | Shaffy OS, with `power.hibernation` | `power.low_battery_hibernate` needs a hibernation that resumes. |
| [Secure Boot](#secure-boot) | You, by hand: Shaffy OS's tool and the firmware's menu | Hibernation must still resume under it, and fwupd's firmware updates need your signature. |
| [Boot splash](#boot-splash) | Shaffy OS, with `desktop.boot_splash` | With `login.autologin`, its prompt is the only password at start-up. |
| [Firmware](#firmware) | Shaffy OS installs fwupd; you install the updates and clean the boot entries | `./bin/shaffy outdated` lists firmware updates. |
| [Networks](#networks) | You, in NetworkManager | The home backup leaves out NetworkManager's profiles. |

Disk encryption and a Windows dual boot are installer choices
([supported baseline](../README.md#supported-baseline)). After installing, set
up swap and hibernation before Secure Boot, which signs what they configured.
The boot splash needs no place in that order: it is rebuilt through the
distribution's hooks, which sign again what Secure Boot covers. Keep a recovery
medium at hand: firmware keys and disk layout changes are outside snapper's
reach.

## Swap and hibernation

CachyOS swaps to zram, compressed memory, which cannot hold a hibernation image.
Hibernation needs a disk swap at least as large as the memory in use, and a
boot that knows where to resume from. Shaffy OS sets both up when the
supplement sets `power.hibernation`:

```yaml
power:
  hibernation:
    swap_gib: 32   # about the size of the memory: grep MemTotal /proc/meminfo
```

The `power` role then makes a swap file of that size in a Btrfs subvolume of its
own, outside snapper's snapshots, at a lower priority than zram, so zram stays
the everyday swap. It adds the file to `/etc/fstab`, adds `resume=` and
`resume_offset=` to the kernel command line in a drop-in under
`/etc/limine-entry-tool.d/`, regenerates the Limine entries and checks each
entry's command line.

1. Set `power.hibernation` and apply the `power` role.
2. Reboot, then test a hibernation with work saved and the charger connected.

[Hibernation](POWER-MANAGEMENT.md#hibernation) has the details: what the role
checks before it writes, how it hands over a swap you set up yourself, and how
to remove it.

- **Shaffy OS:** `power.hibernation` prepares the swap and the resume.
  `power.low_battery_hibernate` makes UPower hibernate at a critical battery
  level; enable it only after a manual hibernation has resumed
  ([critical-battery hibernation](POWER-MANAGEMENT.md#critical-battery-hibernation)).
  The `power` role checks that logind reports hibernation available, but that
  does not prove resume works.
- **Sources:** [Btrfs swap files](https://btrfs.readthedocs.io/en/latest/Swapfile.html),
  [swap](https://wiki.archlinux.org/title/Swap),
  [suspend and hibernate](https://wiki.archlinux.org/title/Power_management/Suspend_and_hibernate),
  [zram](https://wiki.archlinux.org/title/Zram),
  [Limine on CachyOS](https://wiki.cachyos.org/configuration/boot_manager_configuration/).

## Secure Boot

Secure Boot makes the firmware start only boot loaders signed with a key it
trusts. On CachyOS with Limine, sbctl creates keys of your own and enrols them
while the firmware is in Setup Mode. The Limine loader is signed with them and
carries the checksum of `limine.conf`, which holds a hash of every kernel and
initramfs image: with Secure Boot on, Limine starts nothing that does not
match. `scripts/configure-secure-boot.py` sets this up as the
[CachyOS guide](https://wiki.cachyos.org/configuration/secure_boot_setup/)
describes, in steps you run by hand, as root, in a terminal. No apply ever runs
it, because enrolment writes firmware variables that no snapshot can restore.

Before you start, set up [swap and hibernation](#swap-and-hibernation) if you
want them, add `sbctl` to `packages.extra` in the supplement and apply the
`packages` role, and know how to reach the firmware's setup. Then, in the
checkout:

1. **Check.** `sudo /usr/bin/python3 scripts/configure-secure-boot.py` reports
   and changes nothing. It names each `problem:`; solve those first.
2. **Put the firmware in Setup Mode.** `systemctl reboot --firmware-setup`
   starts its setup. The Secure Boot menu has an option that deletes the keys
   or resets to Setup Mode; its name depends on the vendor. On ASUS, set the
   mode to Custom and choose Key Management, Delete all Secure Boot Variables;
   on MSI, set the mode to Custom and delete the keys under Key Management; on
   Gigabyte, decline the offer to reset without saving, then save and exit.
   Leave Secure Boot itself off, and start CachyOS.
3. **Prepare.** `sudo /usr/bin/python3 scripts/configure-secure-boot.py --prepare`
   refuses unless the firmware is in Setup Mode. It installs
   `/etc/initcpio/post/sbctl`, which keeps sbctl's mkinitcpio hook from
   signing kernel images: Limine checks them by hash, and signing would change
   them. It creates the keys in `/var/lib/sbctl/keys/`, turns on configuration
   enrolment and file verification in `/etc/default/limine`, adds the hash of
   the boot menu's wallpaper to `limine.conf`, and rebuilds the boot images
   with `limine-mkinitcpio`, whose hooks enrol `limine.conf` in the loader and
   sign it. It then checks the result: the signature, the enrolled checksum,
   every hash on the EFI system partition, and that no file Limine loads lacks
   one.
4. **Enrol.** `sudo /usr/bin/python3 scripts/configure-secure-boot.py --enroll`
   checks the preparation again and enrols your keys with sbctl, with
   Microsoft's beside them. If sbctl refuses because the firmware's variables
   are immutable, it names them and how to make them writable; do that and run
   `--enroll` again.
5. **Turn Secure Boot on** in the firmware's setup and start CachyOS. On ASUS,
   set the OS type to Windows UEFI mode; Other OS keeps Secure Boot off.
6. **Check again.** The report should show Secure Boot on, Setup Mode off,
   this machine's keys in the firmware and no problems. Test a hibernation
   again. If the disk unlocks with the TPM rather than only the passphrase,
   enrolment changed what the TPM measures: type the passphrase, then enrol the
   TPM again with `systemd-cryptenroll`.

Each step that writes first checks that the machine is the
[supported baseline](../README.md#supported-baseline): Limine with CachyOS's
entry tool and boot hooks, the EFI system partition at `/boot` and a
systemd-based initramfs. It refuses before writing anything when the machine
differs, when another `limine.conf` on the EFI system partition would take
precedence, when a setting in `/etc/default/limine` contradicts the ones it
needs, or when sbctl already re-signs a file Limine checks by hash. It keeps its
settings between `# BEGIN Managed by Shaffy OS: Secure Boot` and
`# END Managed by Shaffy OS: Secure Boot` lines, and leaves lines that already
set the same values alone. Before changing a file it copies it to
`/var/lib/shaffy/secure-boot/before-<time>/`, readable by root only, and it
records each step in `/var/lib/shaffy/secure-boot/secure-boot.json`. A step
repeated changes nothing; after enrolment, `--prepare` restores its own files
without Setup Mode.

**Microsoft's keys.** `--enroll` keeps them by default. The firmware needs them
to start Windows's boot manager and the option ROMs of graphics cards and other
devices, whose firmware Microsoft signs. `--no-microsoft` enrols your keys
alone: it prints a warning, and it refuses when Windows's boot manager is on
the EFI system partition or in Limine's menu; a Windows on another disk it
cannot see. sbctl also refuses when the TPM's event log shows option ROMs; the
tool never overrides that. Without those keys, a screen can stay dark at
start-up until Secure Boot is turned off.

**What cannot be undone.** Enrolment replaces the firmware's key lists, and
`--enroll` names the certificates, other than Microsoft's, that the firmware
will no longer trust. The tool copies the old lists to the same
`before-<time>/` folder for reference, but cannot write them back. The private keys in `/var/lib/sbctl/keys/` are the only
ones that sign for this firmware: they stay on the encrypted disk and out of
every repository, and without them you start again from Setup Mode. The tool
never deletes keys, never uses sbctl's `--firmware-builtin` (it can enrol
duplicate entries that ASUS and Gigabyte firmware then rejects), never clears
the TPM, never reboots and never forces past sbctl's safeguards.

**Recovery.** If the machine does not start with Secure Boot on, because the
firmware reports a Secure Boot violation or Limine stops with an error, start
the firmware's setup and turn Secure Boot off: the firmware then starts the
loader whatever its signature, and your keys stay enrolled. Should Limine still
stop, because `limine.conf` or a file it names no longer matches, start from
the recovery medium and rebuild the boot images from there. Back in CachyOS,
run the check, solve what it reports (often by running `--prepare` again), and
turn Secure Boot on again. To return to the vendor's keys, use the firmware's
option that restores the factory keys.

The firmware's fallback loader, `/boot/EFI/BOOT/BOOTX64.EFI`, is either
Windows's boot manager or an unsigned copy of Limine, which CachyOS's tools do
not sign. Should the firmware lose its entry for Limine, after a firmware
update or a clean-up of the boot entries, it cannot start Limine from there
under Secure Boot: turn Secure Boot off, start CachyOS, and run
`sudo limine-install` to register Limine again. The check notes an unsigned
Limine fallback.

- **Shaffy OS:** `power.hibernation` and the boot splash rebuild the boot
  images with `limine-mkinitcpio`, as kernel updates do. Its hooks enrol the
  new `limine.conf` and sign the loader again, so they keep working under
  Secure Boot. Hibernation fails while the kernel's lockdown mode is active:
  the check reports lockdown and whether this boot can resume. `--prepare` also
  signs fwupd's EFI program ([firmware](#firmware)).
- **Sources:** [CachyOS Secure Boot setup](https://wiki.cachyos.org/configuration/secure_boot_setup/),
  [sbctl](https://github.com/Foxboron/sbctl),
  [Secure Boot](https://wiki.archlinux.org/title/Unified_Extensible_Firmware_Interface/Secure_Boot),
  [Limine's usage notes](https://codeberg.org/Limine/Limine/src/branch/trunk/USAGE.md),
  installed in `/usr/share/doc/limine/`. Limine 12 requires a hash on every
  file only under Secure Boot with a configuration checksum enrolled, and skips
  a wallpaper whose hash does not match instead of stopping.

## Boot splash

Plymouth draws the screen between the boot loader and the login, and asks for
the disk password there. Shaffy OS sets its own theme, `shaffy-os`: CachyOS's
`cachyos` theme, with the firmware's logo, CachyOS's spinner and password
prompt, and a plain Shaffy OS wordmark at the bottom where CachyOS draws its
logo. The `boot_splash` role, part of every system apply, installs CachyOS's
theme package (`cachyos-plymouth-theme`) and puts `shaffy-os` in
`/usr/share/plymouth/themes/`: its theme file and wordmark from this
repository, its other images copied from CachyOS's theme. It selects the theme
in `/etc/plymouth/plymouthd.conf` and rebuilds the boot images once. On its
own:

```sh
./bin/shaffy apply --system --tags boot_splash
```

The role keeps a copy of each file before it changes it, and its record, under
`/var/lib/shaffy/`; an apply interrupted before the rebuild rebuilds on the
next run. It refuses before writing when Plymouth or `limine-mkinitcpio` is
missing or the kernel command line has no `splash`. The new splash shows from
the next boot.

To keep another theme, name its folder under `/usr/share/plymouth/themes/` in
the supplement; the theme must be installed before the apply that selects it.
`false` leaves Plymouth's selection alone:

```yaml
desktop:
  boot_splash: cachyos   # or false
```

The role replaces only a selection that CachyOS's installer made
(`cachyos-bootanimation`, or `cachyos`) or that Shaffy OS made itself. Finding
another theme selected, such as one your supplement's tools installed, it
refuses and names it, so that theme stays until you set `desktop.boot_splash`
to it. Once the setting names it, Shaffy OS manages the selection, and a later
value replaces it.

- **Secure Boot:** the role rebuilds with CachyOS's `limine-mkinitcpio`, as a
  kernel update does, never with `plymouth-set-default-theme -R` or a plain
  `mkinitcpio`. Its boot hooks renew the hashes in Limine's configuration, enrol
  that configuration in the loader and, with Secure Boot set up, sign the
  loader again with your keys. The role touches no keys and never reboots.
- **Shaffy OS:** with `login.autologin`, the Plymouth prompt is the only
  password at start-up: SDDM starts Niri and the keyring opens with the same
  password ([login keyring](LOGIN-KEYRING.md)).
- **The wordmark** is drawn from SVG by `scripts/build-splash-wordmark.py`, with
  `rsvg-convert` and the desktop's JetBrainsMono Nerd Font, and kept as a PNG,
  so installing needs no graphics tools. Run it again after changing the
  drawing.
- **Sources:** [Plymouth](https://wiki.archlinux.org/title/Plymouth),
  [Limine on CachyOS](https://wiki.cachyos.org/configuration/boot_manager_configuration/),
  [CachyOS's Plymouth theme](https://github.com/CachyOS/cachyos-plymouth-theme).

## Firmware

The firmware keeps its own list of boot entries, which survives a reinstall:
earlier installations and experiments leave entries that point nowhere.
`efibootmgr` lists them with their numbers; delete one that is certainly dead
with `sudo efibootmgr --bootnum XXXX --delete-bootnum`, and keep the entries for
Limine, Windows and the firmware's own tools.

Shaffy OS installs `fwupd` and switches on its daily metadata refresh, so
`./bin/shaffy outdated` lists firmware updates for the machine and its devices:
the system firmware, disks, docks and receivers whose vendors publish on the
Linux Vendor Firmware Service. Install them yourself with `fwupdmgr update`,
with the charger connected; some need a reboot. `fwupdmgr get-devices` shows
what fwupd supports. Firmware it does not cover comes through the vendor's own
tools, often from Windows or the firmware setup.

With [Secure Boot](#secure-boot) and your own keys, the system firmware's
updates run through fwupd's EFI program, which then needs your signature, and
fwupd must start it without shim, which CachyOS does not have. The Secure Boot
tool's `--prepare` does both: it signs `/usr/lib/fwupd/efi/fwupdx64.efi` with
`sbctl sign -s`, which records it, so sbctl signs it again after each fwupd
update, and it sets `DisableShimForSecureBoot=true` under `[uefi_capsule]` in
`/etc/fwupd/fwupd.conf`, between its markers. Shaffy OS installs fwupd before
you would set up Secure Boot; if it arrived later, run `--prepare` again.

A firmware update can put back the vendor's Secure Boot keys or turn Secure
Boot off. With Secure Boot set up, run the tool's check after one; if the
firmware no longer holds your keys, put it in Setup Mode and run `--enroll`
again.

- **Sources:** [efibootmgr](https://wiki.archlinux.org/title/Unified_Extensible_Firmware_Interface#efibootmgr),
  [fwupd](https://wiki.archlinux.org/title/Fwupd).

## Networks

NetworkManager keeps each network's profile, passwords included, in
`/etc/NetworkManager/system-connections/`, readable by root only. That is
outside both repositories, as secrets should be, and outside the home backup,
so after a reinstall you connect to each network again. Settings a network
needs beyond its password, such as a pinned band for an access point that
fails on 5 GHz (`nmcli connection modify NAME 802-11-wireless.band bg`), are
worth a line in your supplement's notes. For eduroam, use your institution's
installer from geteduroam or the eduroam Configuration Assistant Tool, which
sets the certificate and the server name check that a hand-made profile often
lacks.

- **Sources:** [NetworkManager](https://wiki.archlinux.org/title/NetworkManager),
  [geteduroam](https://www.geteduroam.app/),
  [eduroam CAT](https://cat.eduroam.org/).
