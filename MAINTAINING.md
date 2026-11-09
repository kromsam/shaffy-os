# Maintaining Shaffy OS

What the maintainers do beyond [the contract](AGENTS.md) every contributor
follows: integrating changes, releasing them, keeping the repository's pins
and transitions current and retiring workarounds once upstream fixes them.

## Branches and releases

`main` holds the releases and `dev` the integrated changes. Pull requests target
`dev`. Maintainers work on `dev` in a separate worktree, so the checkout their
own machine is bound to stays on `main`; a `workshop` worktree started from
`main` can hold a quick experiment on the machine, and whatever should stay is
carried to `dev`. Try either on the machine with `./bin/shaffy apply
--checkout PATH`, run in its worktree. Never commit to `main` directly, and
delete a branch once it is merged.

A release merges `dev` into `main` once its self-test and CI pass:

1. Fast-forward `main` to `dev`.
2. Tag the new `main` with a signed tag named after the day, `vYYYY.MM.DD`
   (`vYYYY.MM.DD.1` and so on for another release that day). The tag is the
   release that `minimum_release` in a supplement names, and that
   `./bin/shaffy status` reports (`lib/shaffy/source.py`).
3. Push `main` with the tag, and publish a GitHub release from the tag. Its
   notes say what changes for an installation, and what a supplement must
   change with it.

## Pins and transitions

This repository pins what it cannot take as `latest`: the Ansible collections,
the Yaru icons, the Better BibTeX release a new Zotero profile gets, yabridge's
revision, the CI container and its checkout action, the LazyVim plugin lock and
ruff's Python target, which follows the system Python. Packages and mise tools
have no pins here. `./bin/shaffy outdated` reports the pins that are behind and
the transitions whose `Remove after` date has passed. Look about once a month,
and raise the pins on `dev`:

```sh
# in the dev worktree
python3 scripts/refresh-pins.py --check   # what is behind; changes nothing
python3 scripts/refresh-pins.py           # raise every pin it can
git diff
./bin/shaffy selftest
```

The script refuses to change `main`. For each pin it takes the newest stable
release, at least three days old where the upstream records a date (Galaxy
does), recomputes the SHA-256 of a download that is checked against one, and
rewrites every file that names the pin, the docs included. The CI container
follows `cachyos/cachyos:latest`, and the checkout action its newest release
tag. A pin it cannot raise is reported and left as it was, and the run then
exits with an error.

The LazyVim lock counts as behind 30 days after its last commit; pass
`--lazyvim` to refresh it sooner. The script copies the Neovim configuration
into a throwaway home, lets every plugin move with `SHAFFY_LAZY_REFRESH` set
(`lua/config/lazy.lua` otherwise holds each plugin to the lock), runs
`Lazy! sync` twice, because LazyVim discovers some plugins only once it is
installed, and keeps the lock that results. It names plugins that were added
or removed.

yabridge is reported only. `scripts/build-audio-bridge.sh` builds the official
`new-wine10-embedding` branch at the revision it names, because the stable
release's GUI embedding does not work with newer Wine. To take a newer
revision, read the upstream changes since the pinned one, set `revision=` in
the script on `dev` and release it; each installation then rebuilds
([maintenance](docs/MAINTENANCE.md#windows-audio-plugins-reaper)).

Commit the refreshed pins on `dev`. Once CI passes and `dev` is released, the
next `./bin/shaffy update` on each machine installs the new Yaru icons, the new
collections and the plugins of the new lock ([Updating](docs/UPDATING.md)).

Retirements and migrations carry a `Remove after YYYY-MM-DD` comment
([AGENTS.md](AGENTS.md#retirements-and-migrations)). Delete each one that
`outdated` lists on `dev`, with everything it names, and release it like a pin
refresh.

## Upstream workarounds

### The folder chooser

Until Niri includes [fix d054395](https://github.com/niri-wm/niri/commit/d054395)
for [issue 4691](https://github.com/niri-wm/niri/issues/4691), Nautilus 51.1
crashes when an XWayland application asks for a folder. Shaffy OS therefore
chooses files and folders with GTK's chooser:
`home/dot_config/xdg-desktop-portal/niri-portals.conf` is Niri's own portal
preferences plus one `FileChooser=gtk` line. After a Niri update:

1. Check that the running Niri (`niri msg version`, after logging in again)
   includes d054395; a higher version number alone does not prove it.
2. Remove the `FileChooser` line from that file, apply the home with
   `--checkout` from the `dev` worktree, and restart the portal with
   `systemctl --user restart xdg-desktop-portal.service`.
3. Choose a folder from an XWayland application, for example a local project in
   Codex. Nautilus's chooser should open and return the folder, and
   `journalctl --user --since '5 minutes ago'` should show no Nautilus crash and
   no `Delegated FileChooser call failed`. If it fails, restore the line.

When the line is gone and Niri's preferences equal the file, delete the file and
retire it in `home/.chezmoiremove`. A test compares the file with the installed
`/usr/share/xdg-desktop-portal/niri-portals.conf`, so a Niri update that changes
its preferences shows up there.
