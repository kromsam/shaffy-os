# Maintenance commands

Recurring commands for the tools Shaffy OS keeps beside its applies. Run them
from the checkout as the desktop user unless noted. Tools that exist only for
one particular laptop's hardware and data are documented outside the public
export.

## Windows audio plugins (REAPER)

The stable yabridge's GUI embedding is incompatible with newer Wine, so
`scripts/build-audio-bridge.sh` builds the official `new-wine10-embedding` branch
at exact revision `ba7022df0aee1e91cde62d7f0e940d3bc43a82b0` and installs it with a
hash receipt under `~/.local/share/yabridge` (64-bit VST2 only). Wine Staging and
the build dependencies come from the `workflows` package group.

`./bin/shaffy outdated` reports when the branch has moved past the revision. To
take a newer one, read the upstream changes, set the revision here and in the
script on dev and release it. Then remove `~/.cache/shaffy/build/audio-bridge`,
where the build keeps its source at the old revision, and run the script again.

`scripts/reload-reaper.sh` requests normal REAPER closure (allowing a save
prompt), applies the home only after REAPER exits, then opens a new empty project.
It never discards unsaved work. REAPER's PipeWire audio keys and the yabridge
configuration belong to the `reaper` integration; the rest of `reaper.ini` stays
REAPER's.

## Integrations

Optional integrations are listed in `defaults/integrations.yml`: `backup`,
`nextcloud`, `reaper`, `ssh`, `syncthing`, `zettlr` and `zotero`. A
supplement requests each with `integrations: {name: managed|disabled|released}`;
one it does not name stays unmanaged. Their files render only while managed, and
the ownership ledger (`~/.local/state/shaffy/ledger.json`) records the activation
an apply created, so `disabled` removes exactly those links and autostart entries
and keeps configuration and data. `./bin/shaffy status` lists what the next apply
would change or refuses. When an integration is already active on a machine,
`./bin/shaffy integration NAME adopt` records it as managed first; `release`
stops tracking it and changes nothing. A managed integration that disappears
from the settings is refused until it is set to `disabled` or `released`.

Zotero names a new profile directory at random, and the `zotero` integration
manages `~/.zotero/zotero/shaffy.default`. A machine where Zotero already made a
profile moves it once before adopting: close Zotero, run
`scripts/move-zotero-profile.py` to see the plan, then again with `--apply`.
Inside a snapper pair (config `home`) it renames the profile directory and
changes only the `Path=` line of `profiles.ini`, keeps a copy of the old file
under `~/.local/state/shaffy/runs/` and prints how to undo it; a run interrupted
halfway is finished by running it again. Start Zotero once to check the library,
then run `./bin/shaffy integration zotero adopt`.

## Publishing tools

Quarto is a mise tool, which `scripts/install-workspace-tools.sh` installs and
updates. qpdf is a package of the `writing` group.

## Synchronization

Nextcloud and Syncthing keep four things apart:

- **Onboarding** is the applications' own: sign in to the Nextcloud account and
  choose folders in its window; pair devices and share folders in Syncthing's
  web interface (`http://127.0.0.1:8384`). Accounts, device identities and
  folder mappings stay out of both repositories.
- **Configuration** that is public (the Nextcloud autostart entry, Syncthing's
  user unit) comes from the `nextcloud` and `syncthing` integrations. A home
  apply never edits the clients' own settings, so it never rewrites a mapping,
  replays a path move or resumes a paused folder or device.
- **Adoption** of a machine that already synchronizes: review the running
  clients read-only, then record their activation as managed.

  ```sh
  python3 scripts/inspect-nextcloud-runtime.py   # accounts, mappings, pause states
  python3 scripts/inspect-sync-runtime.py        # device name, peers, folder health
  ./bin/shaffy integration nextcloud adopt
  ./bin/shaffy integration syncthing adopt
  ```

- **Activation** is `managed` in the settings: the next apply links Syncthing's
  service and Nextcloud's autostart entry. Resuming a folder paused for review is
  a separate, deliberate step for exactly one folder, for example
  `python3 scripts/inspect-nextcloud-runtime.py --resume ~/Documents`. It takes
  a snapper pair (config `home`) first and refuses without one; to undo, pause
  the folder again in Nextcloud and compare the pre snapshot with the home. In
  Syncthing, resume folders and devices in its web interface.

Both reports are written under `~/.local/state/shaffy/runs/sync-runtime/`.
