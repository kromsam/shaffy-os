# Validating a fresh installation in a VM

This kit installs CachyOS in a QEMU virtual machine and applies Shaffy OS to it
as a new user would: first the public defaults, then a supplement with the
optional integrations that work without outside services. It checks what the
sandbox and container tests cannot reach: a real Niri session started by SDDM,
reboots, snapper pre and post pairs taken by snapper itself, services that
start at login, and Noctalia fetching its plugins from its sources. Disk
encryption and automatic login, swap and hibernation, Secure Boot and the boot
splash are outside its scope.

It has two halves:

- `tests/vm/run-vm.sh` runs on the host. It creates the VM, boots the pinned
  installer or the installed disk, and builds the read-only share the guest
  reads the checkout from. It never needs root.
- `guest-validate.sh` runs in the guest, from that share, in stages. Each stage
  prints `PASS`, `FAIL` and `NOTE` lines and keeps the output of every command
  it runs in a second, writable share. The stages run `./bin/shaffy` and sudo
  themselves, which is right only in a disposable VM; they refuse to run
  anywhere but in a QEMU or KVM guest.

## What the host needs

- QEMU with its graphical display: `sudo pacman -S --needed qemu-desktop`. It
  brings the UEFI firmware, `edk2-ovmf`, as a dependency of `qemu-system-x86`.
  The script uses `/usr/share/edk2/x64/OVMF_CODE.4m.fd` with a private copy of
  `OVMF_VARS.4m.fd`, where `edk2-ovmf` 202608 installs them. It also finds the
  paths of older packages and other distributions, and QEMU's firmware
  descriptors in `/usr/share/qemu/firmware/`, and never picks a Secure Boot
  build.
- KVM: `/dev/kvm` must exist and be writable for you.
- About 5 GB of free memory. The guest takes 4 GiB (`--memory MIB` changes it),
  and QEMU needs some beyond that; the script warns when less is available.
- Disk space for the installer image and the guest's disk. The disk holds 40
  GiB but grows only as the guest writes; the system apply installs many
  gigabytes of packages, among them TeX Live, LibreOffice, Wine and Docker.
- The installer image, pinned by path and SHA-256 at the top of `run-vm.sh`
  (`ISO` and `ISO_SHA256`), currently
  `~/Downloads/cachyos-desktop-linux-260809.iso`. To use another image, name
  it with its checksum: `--iso PATH --sha256 SHA256`.
- A network connection. The guest downloads packages, the latest Ansible
  controller, its pinned collections, and Noctalia's plugins. The mise tools
  follow `latest`, so separate VM installations can use different tool versions.

## The host's files

`run-vm.sh` keeps everything in `~/.cache/shaffy/vm/` (`--dir` moves it):

| Path | Contents |
| --- | --- |
| `disk.qcow2` | The guest's disk, created without copy-on-write on Btrfs. |
| `OVMF_VARS.4m.fd` | The guest's UEFI variables, a private copy of the firmware's template. |
| `share/` | Read-only for the guest: `shaffy-os.bundle`, a Git bundle of the checkout's `HEAD` and branch; `guest-validate.sh`; the headless install's `install-headless.sh`, `vm-install.json` and `vm-post-install.sh`; with `--headless`, `authorized_keys`; `supplements/example` (`supplement.example`) and `supplements/vm` (`tests/fixtures/supplements/vm`); `SHARE-INFO`, which names the commit. |
| `evidence/` | Writable for the guest: the stages' evidence and the host's own records. `--evidence DIR` puts it elsewhere; give it an empty folder of its own, since the guest can change anything in it. |

The share is built from the commit at `HEAD` only, so commit a change before
you share it; `run-vm.sh` says when the working tree holds changes the share
leaves out, and refuses to build a share, before it creates anything, while
`HEAD` does not hold this kit. `install` and `run` build the share when it is missing, and
`tests/vm/run-vm.sh share` rebuilds it, also while the VM runs.

The home backup leaves `~/.cache` out, but snapper's `home` snapshots include
it, and they keep the blocks of the guest's disk until snapper removes them.
Delete the folder when the validation is done, or put it outside `/home` with
`--dir`.

## Installing the guest

```sh
tests/vm/run-vm.sh --dry-run install   # what it would create, and the QEMU command
tests/vm/run-vm.sh install
```

`install` checks the image against its pinned SHA-256, creates the disk, the
firmware variables, the share and the evidence folder when they are missing,
and boots the installer. The machine has KVM, the q35 chipset, UEFI firmware,
2 to 4 virtual CPUs (`--cpus N`), 4 GiB of memory, a `virtio-vga-gl` display
shown with `-display gtk,gl=on` so Niri gets a render device, user-mode
networking through `virtio-net`, a tablet pointer, and sound when the host runs
PipeWire.

The image carries CachyOS's command-line installer, `cachyos-installer`, beside
the graphical one; given a configuration with `"headless_mode": true` it installs
without questions. The share holds that configuration, `vm-install.json`, and
two scripts. In the live session that boots, open a terminal and run:

```sh
sudo mkdir -p /run/shaffy-share && sudo mount -t 9p -o trans=virtio,version=9p2000.L,ro shaffy-share /run/shaffy-share && sudo bash /run/shaffy-share/install-headless.sh
```

`install-headless.sh` refuses outside the installer's live session in a QEMU or
KVM guest with one disk, `/dev/vda`. It gives the live session the default
mirror lists the new system will get and skips the installer's mirror ranking:
otherwise the base install downloads from the fastest mirror, the new system's
first sync asks the first mirror of the default list, and when that one lags
behind, pacman pairs the newer database with the older mirror's signature and
the install fails. Then it runs `cachyos-installer --config vm-install.json`,
which:

- erases `/dev/vda`, with a 2 GiB EFI system partition at `/boot` and Btrfs with
  the default subvolumes (`@`, `@home` and siblings) for the rest;
- installs Limine, which on Btrfs also installs `cachyos-snapper-support` and
  `limine-snapper-sync`, so snapper gets its `root` configuration;
- installs the Niri desktop (`cachyos-niri-noctalia` and SDDM), without disk
  encryption: the VM supplement does not ask for automatic login, the one
  feature that needs it;
- creates the account `demo` with the password `demo`, the host name
  `shaffy-vm` and fish as the shell, so the evidence names no real person or
  machine.

Its `post_install` step, `vm-post-install.sh`, then lists the two shares in the
new system's `/etc/fstab`, so they are mounted at every boot. It gives the new
system the `pacman.conf` its base install used: version 0.12.4 installs from the
repositories built for the CPU (such as `cachyos-znver4`) but copies the live
session's `pacman.conf`, which lacks them, so later installs would downgrade to
the generic builds. It also installs
the installer's own Niri profile (`cachyos-niri-noctalia` and SDDM) and enables
SDDM: version 0.12.4 accepts the `niri` desktop but has no entry for it in the
table that maps a desktop to its packages, so a headless install otherwise gets
no desktop. Since the account exists by then, it copies the profile's
`/etc/skel` files into its home as `useradd -m` would have, so Niri starts
Noctalia. The installer fetches current packages from the repositories rather
than the image's snapshot; its log is `/tmp/cachyos-install.log` in the live
session and `/cachyos-install.log` in the new system.

The graphical installer (Calamares) is the other route, the one most people
take. With it, keep its defaults except where this list says otherwise, and
mount the shares by hand as the next section shows:

- **Boot loader:** Limine, the default. With Btrfs it sets up snapper's `root`
  configuration, which Shaffy OS requires.
- **Partitions:** erase the virtual disk, with the default Btrfs file system
  and its default subvolumes. No encryption.
- **Desktop:** Niri, with the Noctalia shell.
- **User:** a normal account with a synthetic name, such as `demo`, and the host
  name `shaffy-vm`, with administrator rights, so it can use sudo.

When the installer restarts the machine, QEMU exits instead. Boot the installed
system from then on with:

```sh
tests/vm/run-vm.sh run
```

Each start writes `host-install-TIME.txt` or `host-run-TIME.txt` into the
evidence folder: the QEMU command, the QEMU and firmware versions, the verified
image checksum and the share's commit.

## In the guest

Log in through SDDM and open a terminal. After the headless install the shares
are already mounted at `/mnt/shaffy-share` and `/mnt/shaffy-evidence`. After a
graphical install, mount them:

```sh
sudo mkdir -p /mnt/shaffy-share /mnt/shaffy-evidence
sudo mount -t 9p -o trans=virtio,version=9p2000.L,ro shaffy-share /mnt/shaffy-share
sudo mount -t 9p -o trans=virtio,version=9p2000.L shaffy-evidence /mnt/shaffy-evidence
sudo chown "$(id -u):$(id -g)" /mnt/shaffy-evidence
```

The mounts end with each reboot. To keep them, add them to the guest's
`/etc/fstab`:

```text
shaffy-share     /mnt/shaffy-share     9p  trans=virtio,version=9p2000.L,ro,nofail  0 0
shaffy-evidence  /mnt/shaffy-evidence  9p  trans=virtio,version=9p2000.L,nofail     0 0
```

Then run the stages in this order, each as
`bash /mnt/shaffy-share/guest-validate.sh STAGE`:

| Stage | When | What it does and checks |
| --- | --- | --- |
| `prerequisites` | First | CachyOS booted through UEFI; `/` and `/home` are the Btrfs subvolumes `@` and `@home`; snapper has its `root` configuration; Niri, Noctalia and SDDM are installed, SDDM is enabled, and the terminal runs in a Niri session with Noctalia running; the guest has a render node; the account can use sudo; the network reaches GitHub; the share holds a valid bundle and both supplements. |
| `bootstrap` | Next | Clones the bundle into `~/Projects/shaffy-os`, or fast-forwards an earlier clone. Runs `check --no-render` to show what is missing, then each command of the Bootstrap section of the top-level README in turn, trusting `mise.toml` if mise asks, as that section says it may. `check` must then report the system scope ready and render the home. |
| `defaults` | Next | `supplement attach --none`, `check` and `diff`; `apply --system`, which must create snapper's `home` configuration; `apply --home`; `status`. Then both applies once more, which must change nothing, and a snapper pre and post pair, marked important, for every apply: `root` and `home` for the system, `home` for the home. |
| `after-reboot` | After a reboot and a fresh login | The guest booted after the last apply; the session is Niri on Wayland, started by SDDM; Niri and Noctalia answer; no user or system unit failed; `docker.service` is active; every managed helper in `~/.local/bin` and `shaffy-desktop-key` is installed; Noctalia fetched and enabled every configured plugin, each from one of its configured sources, and knows both sources; the units of managed integrations are enabled and running; `status` shows nothing unfinished and no drift. Three observations are asked of you: the login screen, the bar and wallpaper, and the launcher on Super+Space, ready for typing. |
| `supplement` | Next | Copies and checks `supplement.example`, as the top-level README's Choose the settings section starts one; copies and checks the VM supplement and previews it with `diff --supplement`; moves the binding to it with `supplement rebind --path PATH --new-id`; applies the system and the home. Each requested integration must become managed, its units enabled and running. It then asks you to start REAPER, Zettlr and Zotero once: a home apply afterwards must set the keys each integration manages in their files, and Zotero's local API must answer. Both applies once more change nothing, `status` names the binding, and the snapper pairs are checked. |
| `after-reboot` | After another reboot | As before, now with the integrations' units. |

The public defaults follow the installed user's XDG directories. No English
home folders need to be created before `defaults`. If testing a supplement
with explicit directory overrides, create or migrate those targets first.
After `defaults` and before the first reboot, follow its
[user-level tool and icon installation steps](../../README.md#apply-and-finish-installation)
inside the guest. The stages validate package and configuration applies; they
do not run those separate installers. Verify the installed tools in the guest
as well: executable launchers alone do not prove that their binaries exist.

The guest keeps the checkout in `~/Projects/shaffy-os` and the two supplement
copies beside it, in `~/Projects/example-supplement` and
`~/Projects/vm-supplement`, as the top-level README recommends. The first system
apply takes a long time: it installs every public package group and builds the
reviewed local packages with makepkg.

A stage asks for the sudo password when it needs root, and ends with its counts
and the next step. Running a stage again continues from what is there: an
existing clone or binding is kept, and the supplement copies are refreshed from
the share.

## Without a window

`--headless` runs the same VM with no window, for a person or an agent working
from a terminal: an offscreen OpenGL display (Niri still gets its render device),
the serial console in `serial-COMMAND-TIME.log` in the evidence folder, a QMP
socket at `qmp.sock` in the VM folder for keys, a VNC socket beside it at
`vnc.sock` for screenshots (QMP's own screendump cannot read the GL display),
and SSH forwarded from `127.0.0.1:2222` only. It creates a throwaway key in
`ssh/` in the VM folder and puts its public half in the share, where the
headless install's `vm-post-install.sh` authorises it for `demo` and enables
sshd. `install --headless` boots the image's own kernel with its live
parameters, a serial console and `systemd.run=`, so the headless install is the
one job the live system boots into (`install-headless.sh --at-boot` starts the
live system's services first); the machine powers off when it finishes, and the
installer's log lands in the evidence folder.

```sh
tests/vm/run-vm.sh --headless install     # returns when the install has finished
tests/vm/run-vm.sh --headless run &       # boot the installed system
tests/vm/drive.py wait-ssh
tests/vm/drive.py login                   # types the password at SDDM
tests/vm/drive.py screenshot screen.png
tests/vm/drive.py stage prerequisites
```

`drive.py stage` runs a stage inside the Niri session through the account's
systemd user manager, under a pseudo-terminal, and answers the password prompts
of sudo and Ansible with the synthetic password. It confirms a pacman
transaction, as a person following README would, unless the transaction
downgrades a package; that one it declines, so the stage fails visibly. It answers a stage's questions
only with rules given to it: `--answer REGEX=REPLY` for the first question the
pattern matches, and `--on REGEX=COMMAND` to run a command in the guest session
first, for example starting REAPER, Zettlr and Zotero; take screenshots before
answering the questions about what the screen shows. A question no rule matches
is skipped, and the stage records it as unanswered. `drive.py keys meta_l-spc`
presses Super+Space to open the launcher, and `drive.py poweroff` shuts the guest
down through SSH and sudo; it presses the power button only when SSH does not
answer, and in a Niri session that suspends the guest instead.

## The VM supplement

`tests/fixtures/supplements/vm/config.yaml` is `supplement.example` with
synthetic identity and writing settings and five managed integrations:
`reaper`, `ssh`, `syncthing`, `zettlr` and `zotero`. The other catalogued
integrations need outside services and stay unrequested: `nextcloud` a
Nextcloud server, `backup` an SFTP repository and one verified backup before its
timers start.
`tests/test_vm_kit.py` fails when `defaults/integrations.yml` gains an
integration that neither list accounts for.

## Evidence

The evidence folder holds:

- `results.txt`: every `PASS`, `FAIL` and `NOTE` line with its time, and each
  stage's counts;
- one folder per stage run, `STAGE-TIME/`, with the stage's results and the
  numbered output of each command: the applies with their Ansible recaps,
  `check`, `diff` and `status`, the snapper lists and pairs, Noctalia's plugins
  and sources, failed units, the boot's error journal and Noctalia's log;
- the host's `host-*.txt` records.

Keep the whole folder with the share's `SHARE-INFO` in your private validation
records. It holds no passwords, since sudo and Ansible read them from the
terminal. The guest's evidence names only the synthetic account and host name,
but the host's `host-*.txt` records hold the host's own paths, your home
folder among them; read the folder before copying any of it anywhere public.

The validation passes when every stage, both `after-reboot` runs included,
reports no `FAIL`. For a failure, fix the cause in the repository, commit it,
run `tests/vm/run-vm.sh share`, run the `bootstrap` stage to update the clone,
and repeat from the failed stage; reinstall when the question is how a fresh
installation behaves. A failed apply leaves its operation unfinished, and
`defaults` and `supplement` refuse to start until it is settled: before
repeating the stage, read `./bin/shaffy status` in the guest's checkout, then
run `./bin/shaffy apply --resume`, or `--abandon` when the captured settings
themselves were wrong.

To start again from nothing, delete `disk.qcow2` and the `OVMF_VARS` copy, or
the whole folder with its evidence, and run `install` again.

## Limits

- The guest has QEMU's virtual hardware: a `virtio-gpu` display rendered through
  the host's OpenGL, no battery, backlight, Bluetooth or phone, and none of the
  host's peripherals.
- QEMU 11.1 can end with a segmentation fault when it exits after a `--headless`
  run (exit status 139), after the guest has shut down; the guest's work is
  unaffected.
- If the host's graphics driver faults while QEMU renders the guest, stop the
  VM and retry with Mesa's software renderer:
  `LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe tests/vm/run-vm.sh --headless run`.
  This keeps the guest's virtual graphics device but uses the host CPU for
  rendering. It is slower and does not validate hardware acceleration.
- `run-vm.sh --dry-run` and `tests/test_vm_kit.py` check the kit without a VM;
  only a person at the VM runs the stages.
