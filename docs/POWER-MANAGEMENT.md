# Hibernation and battery

Three optional settings in the `power` block: [hibernation](#hibernation),
which gives the machine a swap file to hibernate to and the boot entries to
resume from it; [critical-battery hibernation](#critical-battery-hibernation)
on a nearly empty battery; and a [charge limit](#battery-charge-limit).

## Hibernation

CachyOS swaps to zram, compressed memory, which cannot hold a hibernation
image. `power.hibernation` adds a swap file on the disk and tells the kernel
where to resume from:

```yaml
power:
  hibernation:
    swap_gib: 32   # about the size of the memory
```

Make `swap_gib` about the size of the memory (`grep MemTotal /proc/meminfo`).
The kernel compresses the image, so a smaller file often suffices, but a
hibernation with the memory full may not fit. The apply notes when the file is
smaller than the memory. The setting belongs in the supplement.

### What the role does

The `power` role runs `scripts/configure-hibernation.py` as root. Before it
writes anything, it checks that the machine is the
[supported baseline](../README.md#supported-baseline): `/` on Btrfs on one
device, Limine with CachyOS's `limine-entry-tool` and `limine-mkinitcpio`, and
a systemd-based initramfs (`systemd`, and on an encrypted disk `sd-encrypt`, in
mkinitcpio's `HOOKS`). It also refuses, and names the place, when a `resume=`
or `resume_offset=` is set anywhere it did not write itself (another drop-in,
`/etc/default/limine`, `/etc/kernel/cmdline`, or the running boot's command
line), or when `/etc/fstab` already mounts `/swap` or switches on
`/swap/swapfile`: the machine never gets a second resume. Then it:

1. creates the top-level Btrfs subvolume `@swap`, beside `@` and outside
   snapper's configurations, and mounts it at `/swap`;
2. makes the swap file `/swap/swapfile` of `swap_gib` GiB with
   `btrfs filesystem mkswapfile`, readable by root only, and switches it on at
   priority 10, below zram's 100, so zram stays the everyday swap;
3. adds both to `/etc/fstab`, between the lines
   `# BEGIN Managed by Shaffy OS (the power role): power.hibernation` and its
   `# END` line;
4. writes `/etc/limine-entry-tool.d/90-shaffy-hibernation.conf`, which adds
   `resume=UUID=` with the Btrfs filesystem's UUID (on an encrypted disk, this
   names the opened mapping) and `resume_offset=` from
   `btrfs inspect-internal map-swapfile -r` to `KERNEL_CMDLINE[default]`. It
   asks `limine-entry-tool --get-cmdline` for each installed kernel and puts the
   old drop-in back unless every kernel gains exactly these two parameters and
   nothing else changes;
5. regenerates the boot entries with `limine-mkinitcpio`, as a kernel update
   does, and reads each kernel entry of the machine in `limine.conf` back: each
   must hold exactly one `resume=` and one `resume_offset=`, with these values.

The entry tool reads `/etc/default/limine` after the drop-ins, and a
`KERNEL_CMDLINE[default]=` there replaces what they add. Recent CachyOS
installers write that line with `=`, so the role changes it to `+=`, as
`/etc/limine-entry-tool.conf` recommends, after copying the file: the command
line stays the same, and the drop-in can add to it. That is the only change it
makes to the file, and it makes it only when no other file sets the default
command line, whose settings `+=` would otherwise add to the boot; then it
refuses and leaves the change to you.

Kernel updates keep the resume: the drop-in stays, and CachyOS's pacman hook
regenerates the entries with it. Do not hibernate across a kernel update;
reboot into the new kernel first.

An `@swap` subvolume with a swap file of exactly `swap_gib` GiB is adopted
rather than made again. A file of another size is refused, since a swap file in
use is never resized. To change the size, remove the setting and apply, delete
the file ([below](#remove-the-swap-file)), then set the new size and apply
again.

### Secure Boot

`limine-mkinitcpio` runs the hooks in `/etc/boot/hooks/`. With
`ENABLE_ENROLL_LIMINE_CONFIG=yes` in `/etc/default/limine`, the package's
`90-limine-enroll-config` hook enrols the new `limine.conf`'s checksum in the
Limine loader and signs the loader with sbctl's keys, as after every kernel
update. The role refuses to regenerate when enrolment is on and that hook is
missing, since Limine would then refuse its own configuration. It never
touches the keys. Under Secure Boot, hibernation also depends on the kernel's
lockdown mode, so test it again after enabling Secure Boot
([machine setup](MACHINE-SETUP.md#secure-boot)).

### Apply and test

Connect the charger and save work first.

```sh
./bin/shaffy apply --system --tags power
swapon --show                       # /swap/swapfile at priority 10, zram at 100
```

Then reboot. The apply notes when the running boot does not resume from the
swap file yet. After the reboot, `cat /proc/cmdline` shows the `resume=` and
`resume_offset=`, and `cat /sys/power/resume_offset` the same offset. Test a
hibernation with `systemctl hibernate`, with work saved: the session must
return as it was. A second apply should report no changes.

`/var/lib/shaffy/hibernation.json` records what the role owns, written before
each step, so an interrupted apply finishes on the next one. Copies of
`/etc/fstab`, the drop-in and `limine.conf` from before each change are under
`/var/lib/shaffy/hibernation-copies/`.

### Hand over a swap set up by hand

When swap and resume were set up some other way, the role refuses and names
each place. To hand them over, delete those drop-ins, kernel parameters and
`/etc/fstab` lines, keep `/swap` mounted and its swap file switched on, set
`swap_gib` to the file's size, and apply: an `@swap` at `/swap` holding that
file is adopted, and the entries are regenerated with the role's drop-in. A
resume in the running boot that points to the same swap file is accepted; one
that points elsewhere needs a reboot without it first.

### Remove the swap file

Removing `power.hibernation` and applying removes the drop-in and regenerates
the entries, switches the swap file off and removes its `/etc/fstab` lines. The
swap file and `@swap` stay, since they may hold a hibernation image, and `/swap`
stays mounted until the next boot. Once no image is needed, delete them:

```sh
sudo umount /swap
sudo mount -o subvolid=5 "$(findmnt -no SOURCE / | sed 's/\[.*//')" /mnt
sudo btrfs subvolume delete /mnt/@swap
sudo umount /mnt; sudo rmdir /swap
```

Sources: [Btrfs swap files](https://btrfs.readthedocs.io/en/latest/Swapfile.html),
[hibernation into a swap file](https://wiki.archlinux.org/title/Power_management/Suspend_and_hibernate#Hibernation_into_swap_file),
and the entry tool's `/usr/share/doc/limine-entry-tool/README.md` and
`/etc/limine-entry-tool.conf`.

## Critical-battery hibernation

The optional `power.low_battery_hibernate` setting makes UPower hibernate
directly at a chosen battery level. Enable it only after successfully testing
hibernation and resume on the machine. The public desktop leaves CachyOS's
battery policy alone when this setting is absent.

The setting requires `low_percent`, `critical_percent` and `action_percent`,
all greater than zero, at most 100, and strictly descending. They belong in
the supplement; the synthetic example is in
[the comprehensive fixture](../tests/fixtures/supplements/comprehensive/config.yaml).
UPower classifies the warning levels; the desktop controls how warnings appear.

### Apply and verify

Connect the charger and save work first. The `power` role checks that the
installed UPower supports drop-ins (1.91.0 or later) and that logind reports
hibernation available. Availability alone does not prove that resume works.
[`power.hibernation`](#hibernation) prepares the swap file and the resume this
policy needs, but a reboot and a tested hibernation still come first: set it,
apply, reboot and test before adding this policy. The policy does not change
lid-close sleep.

```sh
./bin/shaffy check
./bin/shaffy apply --system --tags power
busctl call org.freedesktop.UPower /org/freedesktop/UPower org.freedesktop.UPower GetCriticalAction
```

The action must be `Hibernate`. The role owns only
`/etc/UPower/UPower.conf.d/90-shaffy-critical-battery.conf`; it leaves the
package's configuration intact and restarts UPower when its override changes.
Read the installed file and check for any later override before relying on the
policy. A second apply should report no changes. Removing the setting and
applying the role removes its override and reloads the packaged policy.
A pending-reload record under `/var/lib/shaffy/` is written before the change
and removed only after a successful restart. Resuming an interrupted apply
therefore loads the policy even when the file is already correct.

A successful manual cycle is a prerequisite, not an automatic-action test.
With work saved and a charger at hand, verify a supervised battery-triggered
cycle separately: UPower must request hibernation and the original session must
resume. Do not keep draining the battery if the action fails. Reboot after a
kernel update before testing hibernation again.

### Evidence and sources

These sources support the mechanism; they do not establish a universal safe
percentage or guarantee any particular hardware.

| Source | What it establishes |
| --- | --- |
| [ArchWiki: low-battery hibernation](https://wiki.archlinux.org/title/Laptop#Hibernate_on_low_battery_level) | Describes direct hibernation at 5% or below and testing the command first. Its separate UPower example uses 20/10/5 with **HybridSleep**, not direct Hibernate. |
| [UPower change 309](https://gitlab.freedesktop.org/upower/upower/-/merge_requests/309), [release history](https://gitlab.freedesktop.org/upower/upower/-/blob/v1.91.5/NEWS) | Version 1.91.2 introduced Auto to delegate to logind, including machines using only zram. It also addressed inhibitors blocking emergency actions. |
| [systemd SleepOperation](https://www.freedesktop.org/software/systemd/man/latest/logind.conf.html#SleepOperation=), [sleep modes](https://www.freedesktop.org/software/systemd/man/latest/systemd-sleep.conf.html) | Auto can select suspend-then-hibernate. That mode initially keeps the session only in RAM and depends on a subsequent wake-up; hybrid sleep saves an image first. |
| [Omarchy issue 13256](https://github.com/omacom/omarchy/issues/13256) | A user reports the battery running out with Auto at 2%, and automatic direct hibernation at 5% succeeding. It records one user's test on one machine, not an upstream policy. |
| [Omarchy v4.0.6 setup](https://github.com/omacom/omarchy/blob/v4.0.6/bin/omarchy-hibernation-setup), [manual](https://github.com/omacom/omarchy/blob/v4.0.6/manual/36-system-sleep.md) | Configures swap and resume, and an ACPI RTC alarm for s2idle; advises checking reliable operation. The setup does not install a critical-battery override. |
| [Proposed fix in a fork](https://github.com/AnPod/omarchy/pull/16) | A direct-hibernation override, proposed in a pull request to a fork of Omarchy rather than to Omarchy itself. |
| [systemd issue 42975](https://github.com/systemd/systemd/issues/42975), [kernel fix](https://github.com/torvalds/linux/commit/f7ecfc3fe5753d10e9c4d0d7955c160ea8cbf0ea) | A related ASUS timed-wake failure was fixed in the AMD Cezanne path. A different machine or an early wake-up needs its own diagnosis. |
| [systemd issue 38193](https://github.com/systemd/systemd/issues/38193) | Reports intermittent failure to proceed from suspend to hibernation; useful follow-up, not proof of an identical cause. |

The role uses UPower's documented configuration interface, not code copied
from the proposed fork. No additional polling daemon is needed for the
battery action. Machine-specific evidence and the chosen thresholds belong
in the supplement.

## Battery charge limit

Many laptops can stop charging below full, which slows the battery's wear when
the machine spends most of its time on the charger. `power.charge_limit` sets
it in the supplement:

```yaml
power:
  charge_limit:
    start: 75   # optional: charge again only below 75%
    end: 80     # stop charging at 80%
```

```sh
./bin/shaffy apply --system --tags power
upower -i "$(upower -e | grep battery_ | head -1)" | grep -i charge
```

UPower reads a battery's levels from the udev hardware database. The `power`
role, through `scripts/configure-charge-limit.py`, writes them to
`/etc/udev/hwdb.d/61-shaffy-charge-limit.hwdb`, the place UPower's own
`60-upower-battery.hwdb` names for local entries, reloads udev and UPower, and
switches the limit on with UPower's `EnableChargeThreshold` for every battery
whose `ChargeThresholdSupported` is true. UPower keeps it on across boots. The
role then reads the kernel's `charge_control_end_threshold` back and fails if
it differs. A battery without support is named and left alone.

Removing the setting removes the entry and switches the limit off again, but
only where the role switched it on (its record is
`/var/lib/shaffy/charge-limit.json`); a limit switched on some other way stays.
To charge fully once, for example before a journey, switch the limit off with
`busctl call org.freedesktop.UPower PATH org.freedesktop.UPower.Device
EnableChargeThreshold b false`, where `upower -e` names the battery's PATH; the
next system apply switches it on again.

Source: [UPower](https://upower.freedesktop.org/docs/) and its
`/usr/lib/udev/hwdb.d/60-upower-battery.hwdb`.
