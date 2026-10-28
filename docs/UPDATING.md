# Updating

Three things move independently: the installed software on the machine, this
repository's own pins, and Shaffy OS releases. Each has one route.

| What | Route | Rhythm |
| --- | --- | --- |
| Packages, the AUR, mise tools and the user installs | `./bin/shaffy update`, in the bound checkout | Weekly |
| This repository's pins | `python3 scripts/refresh-pins.py`, on dev, then a release | Monthly |
| A Shaffy OS release | `git pull --ff-only`, then an apply ([Update](../README.md#update)) | When one is made |

`./bin/shaffy outdated` shows all three at once and changes nothing. It asks
the network, so it takes a few seconds:

```sh
./bin/shaffy outdated            # everything
./bin/shaffy outdated --no-pins  # the machine and the release only
```

## The machine

```sh
./bin/shaffy update                  # in a terminal: pacman and yay ask to confirm, sudo for the password
./bin/shaffy update --skip-packages  # the user-level steps only, without sudo
```

`update` runs these steps in order and stops at the first that fails; fix that
one and run it again. A step with nothing to do is skipped.

1. **System packages**: `sudo pacman -Syu`. snap-pac takes snapper snapshots
   before and after the transaction, and limine-snapper-sync lists them in
   the boot menu, so a bad upgrade can be booted around and rolled back.
2. **AUR packages**: `yay -Sua`. Read the recipe changes yay shows before
   confirming; a recipe is code its maintainer can change at any time.
3. **Ansible controller**: `mise upgrade ansible-core` in the checkout.
4. **Ansible collections**: the exact versions in `requirements.yml`, into the
   checkout's `collections/`.
5. **mise tools and Compound Engineering**: `scripts/install-workspace-tools.sh`,
   for every tool of the mise configuration, Quarto and Zotero MCP among them.
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
  client, and Claude Code and Codex refresh Compound Engineering through their
  plugin managers. `outdated` lists which of them are installed.
- **yabridge**: a reviewed source, rebuilt by hand ([maintenance](MAINTENANCE.md)).
  `outdated` reports a build whose revision differs from the pin.
- **Mason's language tools**: `:Mason` in Neovim, then `U` to update them.
- **Personal pins**: a version the supplement's mise fragment fixes, such as a
  Node.js release, stays where the supplement puts it.

## This repository's pins

The repository pins what it cannot take as `latest`: the Ansible collections,
the Yaru icons, yabridge's revision, the CI container and its checkout action,
and the LazyVim plugin lock. Packages and mise tools have no pins here. Raise them on dev:

```sh
# in the dev worktree
python3 scripts/refresh-pins.py --check   # what is behind; changes nothing
python3 scripts/refresh-pins.py           # raise every pin it can
git diff
./bin/shaffy selftest
```

The script refuses to change main. For each pin it takes the newest stable
release, at least three days old where the upstream records a date (Galaxy
does), recomputes the SHA-256 of a download that is checked against one, and
rewrites every file that names the pin, the docs included. The CI container follows `cachyos/cachyos:latest`, and the checkout
action its newest release tag. A pin it cannot raise is reported and left as
it was, and the run then exits with an error.

The LazyVim lock counts as behind 30 days after its last commit; pass
`--lazyvim` to refresh it sooner. The script copies the Neovim configuration
into a throwaway home, lets every plugin move with `SHAFFY_LAZY_REFRESH` set
(`lua/config/lazy.lua` otherwise holds each plugin to the lock), runs
`Lazy! sync` twice, because LazyVim discovers some plugins only once it is
installed, and keeps the lock that results. It names plugins that were added
or removed.

yabridge is reported only. Read the upstream changes on its
`new-wine10-embedding` branch, then set the new revision in
`scripts/build-audio-bridge.sh` and [maintenance](MAINTENANCE.md) by hand.

Commit the refreshed pins on dev. Once CI passes and dev is released, the next
`./bin/shaffy update` on the machine installs the new Yaru icons, the new
collections and the plugins of the new lock.
