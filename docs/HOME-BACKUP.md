# Home backup

`restic-home` backs up the whole home to one restic repository over SFTP, verifies
every new snapshot and keeps retention to this home's own snapshots. Restic is
installed by the `home_backup` role (`./bin/shaffy apply --system --tags
home_backup`); everything else belongs to the home.

## Settings

The supplement's `backup` settings name the repository and how this home's
snapshots are recognised:

```yaml
backup:
  host: laptop                 # snapshot hostname
  tags: [automatic, shaffy]    # every new snapshot carries all of them
  repository:
    id: "<64 hex digits>"      # from restic-home inspect or init
    sftp: {host: nas.example, port: 2222, user: restic-laptop, path: repository,
           host_key: "ssh-ed25519 AAAA..."}
  excludes: ["~/Projects/some-database/data"]    # beside the public caches
  retention: {daily: 14, weekly: 8, monthly: 12} # the defaults
```

Host, home path and tags together select this home's snapshots, for listing,
verification and retention alike; keep them when reinstalling, so the new
installation continues the old series. The home apply renders
`~/.config/restic-home/{backup.json,ssh_config,known_hosts,excludes}` from them;
without `backup` settings nothing of the client is rendered. SSH accepts only the
pinned host key. The repository password and SSH key are never in either
repository: they are `~/.config/restic-home/password` and `id_ed25519` (0600),
created or restored by hand.

## Lifecycle

- **Create.** With the settings but no `repository.id` yet, `restic-home init`
  creates a new repository at the endpoint (it refuses one that exists) and
  prints its ID. Put the ID into `backup.repository.id` and apply the home.
- **Adopt.** For an existing repository, `restic-home inspect` prints its ID,
  whether it matches the settings, and every snapshot in it, so the ID and the
  host and tags of the series to continue can go into the settings. Every other
  mode refuses a repository whose ID differs from the settings.
- **Verify.** `restic-home backup` makes a snapshot and verifies it from its exact
  ID before it writes the receipt,
  `~/.local/state/shaffy/home-backup/last-success.json`. A marker written just
  before the backup must come back identical, proving the snapshot holds this
  run; then up to three random files that did not change during the run are
  restored and compared with the home. Samples come from outside hidden
  directories, where keys and credentials live, because the receipt records their
  SHA-256. `restic-home verify` repeats the comparison against the receipt later.
  The weekly maintenance runs `restic check`; the monthly check reads all data.
- **Activate.** The `backup` integration (`integrations: {backup: managed}`) links
  the three timers into `timers.target.wants`. Until the receipt in
  `~/.local/state/shaffy/home-backup/` records a verified backup of the
  configured repository, an apply leaves the timers out and `./bin/shaffy status`
  says that the integration waits, so the schedule never starts on an untested
  configuration. A machine whose timers are already enabled records them with
  `./bin/shaffy integration backup adopt`.
- **Disable.** `backup: disabled` removes exactly those three links; the
  configuration, credentials, repository and receipt stay. Running services stop
  at the next logout or with `systemctl --user stop`.

## Behaviour

Each run backs up the home with `--one-file-system` and `--exclude-caches`.
The public exclusions are regenerable caches and the Trash; the settings add
personal ones. Routine backup never prunes. Maintenance first inspects its
dry-run selection and refuses any snapshot outside this home's host, path and
tags, then prunes and checks.

Only the home is backed up. Container volumes, such as Docker's under
`/var/lib/docker`, are outside it: export their databases into the home with each
database's own dump tool, and repeat the export after changes worth keeping.

Private backup progress and errors are in `~/.cache/restic-home/backup.jsonl` and
`backup.stderr`. The user services serialize operations with a local lock and the
repository's locks, and run with the home read-only and only
`~/.cache/restic-home` and `~/.local/state/shaffy/home-backup` writable.

## Schedule and manual commands

- Daily backup: 01:15, with up to ten minutes of random delay.
- Weekly maintenance: Saturday 04:30, with up to twenty minutes of random delay.
- Monthly full repository-data check: first day of the month at 05:30, with up
  to thirty minutes of random delay.

These are user timers with `Persistent=true`; they run while the user manager is
active and catch up missed runs when started. They do not wake a powered-off
laptop, and without linger they wait for a login.

`restic-home status` shows the last receipt, `restic-home snapshots` this home's
snapshots and `restic-home retention-plan` what maintenance would remove. Start a
background backup with `systemctl --user start --no-block
restic-home-backup.service`. `restic-home dry-run` scans without creating a
snapshot; `restic-home check` reads and verifies all repository data.

## Validation

The tests create a temporary repository with a snapshot of another host, back up
a synthetic home, and check that verification restores the marker and random
unchanged files but never hidden or excluded ones, that a file changed after the
backup is not sampled, that retention selects only this home's snapshots, that
corrupted samples, an unpinned or different repository and settings rendered for
another home are refused, and that an earlier version's receipt counts until this
version writes one.

References: [Restic backup behaviour](https://restic.readthedocs.io/en/stable/040_backup.html),
[repository checks](https://restic.readthedocs.io/en/stable/045_working_with_repos.html),
and [restore operations](https://restic.readthedocs.io/en/stable/050_restore.html).
