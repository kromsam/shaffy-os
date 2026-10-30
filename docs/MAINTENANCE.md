# Maintenance commands

Recurring commands for the tools Shaffy OS keeps beside its applies. Run them
from the checkout as the desktop user unless noted. Tools that exist only for
one particular laptop's hardware and data are documented outside this
repository.

## Windows audio plugins (REAPER)

The stable yabridge's GUI embedding is incompatible with newer Wine, so
`scripts/build-audio-bridge.sh` builds the official `new-wine10-embedding` branch
at the exact revision the script names and installs it with a hash receipt under
`~/.local/share/yabridge` (64-bit VST2 only). Wine Staging and the build
dependencies come from the `workflows` package group.

`./bin/shaffy outdated` reports when the branch has moved past the revision. To
take a newer one, read the upstream changes, set the revision in the script on
dev and release it. Then remove `~/.cache/shaffy/build/audio-bridge`,
where the build keeps its source at the old revision, and run the script again.
`python3 scripts/test-windows-vst2.py` then loads every bridged plugin under
`~/.vst/yabridge` and exercises its parameters and audio processing.

`scripts/reload-reaper.sh` requests normal REAPER closure (allowing a save
prompt), applies the home only after REAPER exits, then opens a new empty project.
It never discards unsaved work. REAPER's PipeWire audio keys and the yabridge
configuration belong to the `reaper` integration; the rest of `reaper.ini` stays
REAPER's.

## Integrations

The integrations, their states, `adopt` and `release` are described in
[the supplement](SUPPLEMENT.md#integrations).

Zotero names a new profile directory at random, and the `zotero` integration
manages `~/.zotero/zotero/shaffy.default`. A machine where Zotero already made a
profile moves it once before adopting: close Zotero, run
`scripts/move-zotero-profile.py` to see the plan, then again with `--apply`.
Inside a snapper pair (config `home`) it renames the profile directory and
changes only the `Path=` line of `profiles.ini`, keeps a copy of the old file
under `~/.local/state/shaffy/runs/` and prints how to undo it; a run interrupted
halfway is finished by running it again. Start Zotero once to check the library,
then run `./bin/shaffy integration zotero adopt`.

## Synchronization

Nextcloud and Syncthing keep four things apart:

- **Onboarding** is the applications' own: sign in to the Nextcloud account and
  choose folders in its window; pair devices and share folders in Syncthing's
  web interface (`http://127.0.0.1:8384`). Accounts, device identities and
  folder mappings stay out of both repositories.
- **Configuration** that is public (the Nextcloud autostart entry, Syncthing's
  user unit) comes from the `nextcloud` and `syncthing` integrations. A home
  apply never edits the clients' own settings.
- **Adoption** of a machine that already synchronizes records the clients'
  existing activation as managed, with `integration NAME adopt`
  ([integrations](SUPPLEMENT.md#integrations)).

- **Activation** is `managed` in the settings: the next apply links Syncthing's
  service and Nextcloud's autostart entry.
