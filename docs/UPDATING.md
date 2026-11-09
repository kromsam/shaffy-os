# Updating

Two things move independently: the software installed on the machine, and
Shaffy OS itself, which arrives as releases. Each has one route.

| What | Route |
| --- | --- |
| Packages, the AUR, mise tools and the user installs | `./bin/shaffy update`, in the bound checkout ([The machine](#the-machine)) |
| A Shaffy OS release | Review it, `git pull --ff-only`, then an apply ([A release](#a-release)) |

`./bin/shaffy outdated` shows both at once and changes nothing. It asks the
network, so it takes a few seconds:

```sh
./bin/shaffy outdated --no-pins  # the machine and the release
./bin/shaffy outdated            # also this repository's pins and expired transitions
```

The last two sections of the full report, the repository's pins and the
removals past their date, are the maintainers' work. They reach the machine
with a later release ([MAINTAINING.md](../MAINTAINING.md#pins-and-transitions)).

## The machine

```sh
./bin/shaffy update                  # in a terminal: pacman and yay ask to confirm, sudo for the password
./bin/shaffy update --skip-packages  # the user-level steps only, without sudo
```

`update` runs these steps in order and stops at the first that fails; fix that
one and run it again. A step with nothing to do is skipped.

1. **System packages**: `sudo pacman -Syu`. Where snap-pac is installed, as
   `cachyos-snapper-support` installs it on the
   [supported baseline](../README.md#supported-baseline), snapper takes a
   snapshot of `/` before and after the transaction. With the Limine boot
   loader and `limine-snapper-sync`, those snapshots also appear in the boot
   menu, so a bad upgrade can be booted around and rolled back. Without them,
   the snapshots are there to compare and restore files from, but not to boot.
2. **AUR packages**: `yay -Sua`. Read the recipe changes yay shows before
   confirming; a recipe is code its maintainer can change at any time.
3. **Ansible controller**: `mise upgrade ansible-core` in the checkout.
4. **Ansible collections**: the exact versions in `requirements.yml`, into the
   checkout's `collections/`.
5. **mise tools, the agent plugins and Better BibTeX**: `scripts/install-workspace-tools.sh`,
   for every tool of the mise configuration, Zotero MCP among them, and of the
   supplement's mise fragment, pstack and the supplement's other Claude Code
   plugins, and Better BibTeX when the managed Zotero profile has none.
   mise's `latest` is the newest release that is at least three days old
   ([workspace tools](WORKSPACE-TOOLS.md)).
6. **Yaru icons**: downloads and installs the pinned release, only when the
   installed one differs.
7. **LazyVim plugins**: `nvim --headless "+Lazy! restore" +qa`, only when the
   installed plugins differ from the managed `lazy-lock.json`. It returns them
   to the lock; it never moves the lock.

`update` refuses to start while an operation is unfinished
(`./bin/shaffy status`), and it uses the pins of the checkout it runs in.
Configuration does not change; a release still reaches the machine through an
apply. Run it from the bound checkout, so that the pins match what the applies
use.

### What it leaves alone

- **Applications that update themselves**: SilverBullet Desktop and Beeper
  Desktop download new AppImages themselves, Spotify's launcher updates the
  client, Claude Code and Codex refresh pstack through their
  plugin managers, and Zotero updates Better BibTeX. `outdated` lists which of
  them are installed.
- **yabridge**: a reviewed source, rebuilt by hand
  ([maintenance](MAINTENANCE.md#windows-audio-plugins-reaper)). `outdated`
  reports a build whose revision differs from the checkout's pin.
- **Firmware**: `outdated` lists the updates fwupd knows of; install them with
  `fwupdmgr update`, with the charger connected
  ([machine setup](MACHINE-SETUP.md#firmware)). With Secure Boot set up, run
  `sudo /usr/bin/python3 scripts/configure-secure-boot.py` after a firmware
  update: some put back the vendor's keys or turn Secure Boot off. Kernel
  updates need nothing; the boot hooks enrol and sign again.
- **Mason's language tools**: `:Mason` in Neovim, then `U` to update them.
- **Personal pins**: a version the supplement's mise fragment fixes, such as a
  Node.js release, stays where the supplement puts it.

## A release

Keep the bound checkout on `main`. First see what is coming:

```sh
git fetch
git log HEAD..origin/main   # the commits a pull would bring
```

Read the notes of each new release on
[GitHub](https://github.com/kromsam/shaffy-os/releases). They say what changes
for an installation and what a supplement must change with it. Then pull the
release, install the Ansible collections it pins, and apply:

```sh
git pull --ff-only
mise exec ansible-core -- ansible-galaxy collection install -r requirements.yml -p collections
./bin/shaffy check
./bin/shaffy diff
./bin/shaffy apply --system
./bin/shaffy apply --home
./bin/shaffy update
```

The system apply refuses until the installed collections match
`requirements.yml`. If the checkout's `mise.toml` changed, review it and trust it
again when mise asks. An apply installs what is missing but upgrades nothing;
the closing `./bin/shaffy update` takes the tools and user installs to the
release's pins.

`git pull --ff-only` refuses when the checkout holds commits of its own. Make
personal changes in the supplement, and try a change to Shaffy OS from a
checkout of its own ([Contributing](../README.md#contributing)).

### When a release needs supplement changes

A release that renames, removes or adds a setting says so in its notes. After
the pull, `./bin/shaffy check` reports every problem in the supplement with its
file and key, and every apply refuses until the supplement is valid again, so
the machine stays as it was. Change the supplement as the notes say, run
`check` again, commit the supplement, and apply.

`minimum_release` in the supplement names the oldest release it works with
([config.yaml](SUPPLEMENT.md#configyaml)). Raise it when the supplement starts
to use something a release introduced: on a machine whose checkout is older,
`check` and `apply` then ask for a newer checkout instead of reporting an
unknown key. `./bin/shaffy status` shows the checkout's release.
