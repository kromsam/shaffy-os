# How Shaffy OS works

Shaffy OS is a post-install setup for CachyOS with Niri and Noctalia. chezmoi
renders the home from `home/`, Ansible configures the system through `site.yml`
and `roles/`, and `./bin/shaffy` (the Python package in `lib/shaffy/`) drives
both. It selects and validates the settings, freezes them for each apply, takes
snapper snapshots around every change and records enough to finish or give up
an interrupted run. This document describes that contract as implemented. The
settings themselves are described in [the supplement reference](SUPPLEMENT.md),
and recurring commands in [maintenance](MAINTENANCE.md).

## Public repository and private supplement

| Owner | Contents |
| --- | --- |
| This repository | Defaults (`defaults/config.yaml`), package groups (`defaults/packages/`), the integration catalogue (`defaults/integrations.yml`), the settings schema (`schema/supplement.schema.json`), a minimal example (`supplement.example/`), the chezmoi source (`home/`), the system plays (`site.yml`, `records.yml`, `roles/`), the wrapper, scripts and tests. |
| A supplement | One person's values: `config.yaml`, the native fragments it names under `overrides` and the personal files it names under `assets`. |
| The machine, outside both | Records, journals, the ownership ledger, operation runs and chezmoi's state under `~/.local/state/shaffy/`; root records and root copies under `/var/lib/shaffy/`; the loader cache; snapper snapshots; application data and credentials. |

A supplement is a directory with a `config.yaml`, normally a private Git
repository with its own history. The loader reads only `config.yaml` and the
files it references, so the supplement may also hold notes or tools of its own.
It is selected by path and remembered by a binding, not included as a
submodule. Keep it beside the checkout, for example `~/Projects/shaffy-os` and
`~/Projects/my-supplement`.

A supplement holds identifiers and trust material, never credential values, and
must stay private. Its native fragments are trusted configuration and may run
commands.

The wrapper's records, runs and ledger follow `$XDG_STATE_HOME` when it is set
(`$XDG_STATE_HOME/shaffy/`); this document writes the default. chezmoi's state
file does not follow it: the tracked `chezmoi.toml` names
`~/.local/state/shaffy/chezmoistate.boltdb` with `persistentState`, so plain
`chezmoi --config chezmoi.toml --source home status` reads the same file.

## Settings

### The loader

`lib/shaffy/loader.py` produces the settings for every consumer:

1. Resolve the selection (see [Selection and binding](#selection-and-binding)).
2. Parse `defaults/config.yaml` and the supplement's `config.yaml` with PyYAML,
   restricted to the YAML 1.2 core schema: only `true` and `false` (in lower,
   title or upper case) are booleans, and there are no timestamps, sexagesimal
   numbers or `0`-prefixed octals. A keyboard layout `no` stays a string, and an
   unquoted `0600` is the number 600, so file modes are written as quoted
   strings. A duplicate key is an error.
3. Validate each file with python-jsonschema against
   `schema/supplement.schema.json`, the same schema editors use; the defaults
   carry no `supplement_id`. An unknown `schema_version` is refused. A
   supplement whose `minimum_release` is newer than the newest release tag the
   checkout's HEAD contains is refused before the schema check, with that
   reason alone; the sandboxed render, which cannot see a worktree's Git
   directory, is handed the release found outside it.
4. Merge the supplement over the defaults. Mappings merge key by key; scalars
   and lists replace, so an explicit `false` or `[]` survives. An absent key
   keeps the default, while `key: null` and `key: {}` are errors.
5. Run the checks a schema cannot express. Every `overrides` and `assets`
   reference is relative, exists, and resolves inside the supplement after
   links are followed. A system-scope fragment (`overrides.keyboard_hwdb`) is
   read as text of at most 65,536 characters into the settings, so root never
   reads the user's files. Package choices must agree with the groups: no
   omitted `required` package, no `extra` package that a group already
   installs, and no `remove` of a package that is still installed or that
   Shaffy OS itself needs. Integration requests must name catalogued
   integrations and the settings they depend on. Each enabled MCP server needs a
   command, and backup excludes must keep the backup's verification marker.
6. Emit one JSON object under the key `shaffy`.

That object holds `selection` (mode, path, source and `supplement_id`),
`settings` (the effective values), `overrides` (absolute paths of the
fragments), `assets` (target, path, SHA-256 and the `executable` and
`private` attributes), `system_overrides`, `packages` (`groups` and `aur`
hold each group's repository and AUR selections after omissions, alongside
`extra` and `remove`) and `integrations` (each catalogued
integration with its requested state, scope, targets and activation records).
`./bin/shaffy data` prints it.

### The cached path

The platform's XDG user directories are resolved separately by
`home/.chezmoitemplates/folders.json`, using `xdg-user-dir` through
`lib/shaffy/folders.py`. They are not settings-loader inputs or cached settings:
each home render follows the installation's current directories. Explicit
`folders` settings take precedence, and the user-directory modifier changes
only those named standard entries. Without overrides the XDG file is ignored.

Templates read the settings with `includeTemplate "shaffy.json" . | fromJson`.
`home/.chezmoitemplates/shaffy.json` runs
`/usr/bin/python3 -I -S lib/shaffy/fastload.py` through chezmoi's `output`
function. chezmoi starts it once for every template that asks, so it answers
from a cache:

- The cache lives in `$XDG_RUNTIME_DIR/shaffy/loader/`, or, without a runtime
  directory, in `shaffy/loader/` under `$XDG_CACHE_HOME` (by default
  `~/.cache`). It keeps the 32 newest entries.
- An entry's key is a SHA-256 over the resolved selection and the raw bytes of
  the defaults, the schema, the package groups, the integration catalogue, the
  loader's own modules and the supplement's `config.yaml`.
- Referenced files are stamped rather than hashed: size, modification time,
  inode and change time. An entry also checks that every reference, as
  `config.yaml` writes it, still resolves to the same file, so retargeting a
  link misses the cache.
- A hit imports only the standard library, never PyYAML or jsonschema.
  `./bin/shaffy selftest` checks this with `python3 -X importtime`, and, unless
  run with `--skip-host` as CI runs it, that the fastest of nine runs stays
  under 25 ms and the median under 60 ms.
- A miss runs the full loader. It stores the result only if the inputs are
  unchanged afterwards, so an edit made during the load is never cached under
  the old key. Invalid settings are never stored.

Outside an operation the loader reads the live selection, so an edit to the
supplement appears in the next `chezmoi diff` or `./bin/shaffy diff` without a
regeneration step. The tracked `chezmoi.toml` has no `[data]` table: every
setting comes from the loader. Two alternatives were rejected: chezmoi's
`--override-data-file` breaks every ordinary chezmoi command, which then lacks
the data, and a generated `.chezmoidata` file shows stale settings until it is
regenerated.

### The operation marker

During an apply the wrapper runs chezmoi with four internal environment
variables, after removing any it inherited: `SHAFFY_OP` (the operation ID),
`SHAFFY_OP_MODE=apply`, `SHAFFY_OP_CAPTURE` (the operation's frozen capture)
and `SHAFFY_OP_DEFERRED` (integrations the operation leaves out). The loader
then reads only the capture, and refuses unless the user record's unfinished
operation has that ID and capture.

`./bin/shaffy diff --supplement PATH` (or `--without-supplement`) previews an
explicit selection the same way. It captures that selection into
`~/.local/state/shaffy/runs/<op>/`, journals a `preview` event without recording
an unfinished operation, runs `chezmoi diff` with `SHAFFY_OP_MODE=preview` and
removes the capture afterwards. The loader honours a preview marker only for
chezmoi's read-only commands (`diff`, `status`, `cat`, `verify`, `dump`,
`managed` and `execute-template`), and the guards refuse every changing command
under it. The marker is internal, not a way to select settings.

## Selection and binding

The loader resolves the selection in this order, and never falls back to the
defaults or searches elsewhere:

1. An explicit `--supplement PATH` or `--without-supplement`; the two exclude
   each other.
2. The binding recorded in `~/.local/state/shaffy/binding.json`.
3. Otherwise the public defaults.

A selected or bound supplement that is missing, is not a directory,
has no readable `config.yaml` or fails validation is an error. A bound
supplement that has moved names `./bin/shaffy supplement rebind --path`. No
environment variable selects a supplement.

`check`, `data` and `diff` accept any selection. `apply` uses the binding and
never binds implicitly: `apply --supplement PATH` must name the bound
supplement, and a system apply refuses the option. A new installation binds
once:

```sh
./bin/shaffy supplement attach --supplement ~/Projects/my-supplement
./bin/shaffy supplement attach --none    # the public defaults, deliberately
```

`attach` records the target user, the supplement's resolved path and
`supplement_id`, and the checkout it runs from. It refuses an installation that
is already bound, and one that has a root record, which it recognises only with
`--adopt-system-record` (see [Records](#records)). A binding changes only
deliberately:

```sh
./bin/shaffy supplement rebind --path ~/Projects/my-supplement   # the supplement moved
./bin/shaffy supplement rebind --path PATH --new-id              # accept another supplement_id
./bin/shaffy supplement rebind --checkout ~/Projects/shaffy-os   # apply from another checkout
```

`rebind --path` refuses a different `supplement_id` unless `--new-id` is given.
When the system is bound it first moves the root record's binding through
`records.yml` in the same operation, so sudo asks for the password. `rebind`
refuses while an operation is unfinished. Neither `attach` nor `rebind` applies
anything or changes the ownership ledger. Both, except when adopting a root
record, then list what the binding means for the integrations. To apply once
from a checkout other than the bound one, run `apply` in that checkout and add
`--checkout PATH` naming it; the journal records a `checkout-override` event
and the binding stays as it is.

### Records

Records hold identity, location and progress, never settings values. Each is
replaced atomically (temporary file, fsync, rename, directory fsync) and has an
append-only JSON Lines journal beside it. A torn last line is closed before the
next event is written and skipped when the journal is read.

| Record | Written by | Fields |
| --- | --- | --- |
| `~/.local/state/shaffy/binding.json` (0600), journal `journal.jsonl` | The unprivileged wrapper | `record_version`, `target {uid, user, home}`, `binding {mode, path, supplement_id}`, `checkout {path}`, `system {bound}`, `pending`, `last_completed_op` |
| `/var/lib/shaffy/targets/<uid>.json` (0644), journal `<uid>.journal.jsonl` | Root, through `lib/shaffy/root_op.py`: in a system apply, or through `records.yml` to abandon an operation or move the binding | `record_version`, `target`, `binding`, `pending`, `last_completed_op`, `phases {system: {op, completed_at}}` |

`pending` is null, or the unfinished operation: its ID, scope (`home` or
`system`), capture, phase and start time. The user side also keeps the checkout
it began in, the settings digest, the source manifest and, for a system
operation, its tags.

Before using the user record, `check`, `apply`, `supplement rebind` and
`integration` check that its `target.uid` and
`target.home` belong to the user running the command, so a record made for
another account is refused rather than used.

The root record is expected only when the user record says `system.bound`. A
new apply compares the two records' `target.uid`, `target.home`,
`binding.mode`, `binding.supplement_id` and `binding.path`, and the unfinished
system operation on each side, and refuses on any difference; `status` reports
it. `last_completed_op` and `phases` are never compared. A root record that the
user record does not know, for example after the home was reinstalled, is
refused until one of these recognises it:

```sh
./bin/shaffy supplement attach --supplement ~/Projects/my-supplement --adopt-system-record
./bin/shaffy supplement attach --none --adopt-system-record
```

Adopting requires equal `target.uid`, `target.home`, `binding.mode` and
`supplement_id`, moves the root binding when only the path differs, sets
`system.bound`, and refuses while root holds an operation. It applies nothing.

## Capture

Each apply freezes its inputs before anything reads them. Under umask 077 the
wrapper creates `~/.local/state/shaffy/runs/<op>/capture/` with:

- `selection.json`: the selection (mode, path and source);
- `supplement/`: the supplement's `config.yaml` and every file it references,
  stored under the path `config.yaml` names and with the content that path
  resolves to, so a reference through a link reads the same from the capture;
- `settings.json`: the loader's output for the capture, which a system
  operation's root steps verify and its roles read;
- `manifest.json`: the SHA-256 of each of those files, the digest of the
  effective settings and the `supplement_id`.

The wrapper hashes the supplement's files before copying them, then hashes the
originals and the copies again; any difference refuses the capture, so a
supplement edited during the capture is never half copied. A failed capture is
removed, so no private copy is left without a record. `--resume` and root's copy
recompute the digests and refuse a capture that no longer matches its manifest
or holds files the manifest does not list.

The source manifest covers the framework itself: a SHA-256 over the sorted
paths and content digests of the checkout's tracked and untracked, not ignored
files (`git ls-files -co --exclude-standard`), recorded with `HEAD` and a dirty
flag. It is recorded when an operation begins and compared again later, which
detects a checkout that changes during an operation.

One apply promises consistent supplement inputs for that invocation. It does
not promise immutable package repositories, a whole-system rollback or the
application of an earlier diff; a later apply reads the inputs as they are
then. A revision plus a dirty flag is not a recoverable copy of uncommitted
work; the retained capture is.

Captures and copies live only in the private run directory, `runs/<op>/`, and
failure output goes to the journal. The wrapper never writes under the
checkout on its own: `./bin/shaffy selftest` creates the untracked
`artifacts/` directory some tests need, where ruff also keeps its cache, and
`./bin/shaffy lint --fix` and the agents' edit hook rewrite the source files
they are given, as STYLE.md asks.

## The home apply

`./bin/shaffy apply` (the same as `apply --home`) runs these steps in order:

1. Take the user lock. Refuse without a binding, for another user, when the
   user and root records disagree, or while an operation is unfinished (naming
   `apply --resume` and `--abandon`). Refuse a checkout other than the bound one
   unless `--checkout` names it.
2. Capture the bound supplement. If its `supplement_id` changed, remove the
   capture and refuse, naming `supplement rebind --new-id`.
3. Journal `checkout-override` when `--checkout` is in use, then `begin` with
   the capture, the checkout, the settings digest and the source manifest, and
   write the user `pending` entry with scope `home`.
4. Preflight the whole phase without changing the home:
   - Btrfs on `/` and `/home`, snapper, and a snapper `home` configuration the
     user can read;
   - the captured settings still load, and their digest equals the one taken at
     capture, so a change to the checkout's defaults, schema or loader refuses;
   - the integrations agree with the [ownership ledger](#ownership-ledger);
   - no asset would replace a folder or be written through a link;
   - the desktop key helper (`shaffy-desktop-key`) builds, into
     `$XDG_CACHE_HOME/shaffy/build/`, never under the checkout;
   - a [sandbox render](#the-render-sandbox-and-check) of the capture validates
     and converges;
   - `chezmoi status` succeeds under the marker, and lists what will change;
   - the checkout's source manifest is unchanged since step 3.

   A failure journals `phase-not-started`, clears `pending`, removes the run
   directory and changes nothing.
5. Take the snapper `pre` snapshot of `/home`. Copy into
   `~/.local/state/shaffy/runs/<op>/before/` every existing file chezmoi will
   change and every directory it will delete, except Shaffy OS's own state
   directory, plus the links and autostart entries a disabled integration will
   lose. Journal `phase-start` with the snapshot number and the copies' path.
6. When integrations change state, write their intent to the ledger and journal
   `ledger-intent`. Then run `chezmoi apply --force` under the marker.
7. Run `chezmoi verify` under the marker, install the desktop key helper if it
   differs, and validate the real home with `niri validate`,
   `noctalia config validate` and a parse of every Alacritty TOML file. A
   failure journals `phase-failed` and leaves the operation unfinished.
8. Finish the ledger transitions and journal `ledger-done`.
9. Take the `post` snapshot, paired with the `pre` one.
10. Recompute the source manifest. If the checkout changed while the phase
    wrote, journal `source-changed` and leave the operation unfinished;
    `--resume` completes it with the checkout as it is then.
11. Journal `phase-done`, set `last_completed_op`, clear `pending` and prune old
    runs.
12. Report the snapshot pair and the copies. Then, without failing the apply,
    start the units of newly managed integrations when a user session bus is
    available (otherwise they start at the next login), and, in a running Niri
    session, have Noctalia reload its configuration and templates and check
    Niri's configuration again.

If chezmoi times out on its own state lock in step 6, it has written nothing.
The operation journals `phase-not-started`, withdraws the ledger intent, clears
`pending`, removes the run directory and reports that another chezmoi command is
running. During `--resume` the same timeout journals `resume-refused` and keeps
the operation for another attempt. Any other chezmoi failure journals
`phase-failed` and leaves the operation for `--resume`.

A completed home apply removes the run directories of completed home
operations beyond the newest ten. It never removes the run of an unfinished or
abandoned operation.

## The system apply

`./bin/shaffy apply --system` changes the system through Ansible, run as the
target user. `--ask-become-pass` asks for the sudo password once, in the
terminal, and every task that needs root becomes root through sudo:

```sh
./bin/shaffy apply --system                  # every routine role
./bin/shaffy apply --system --tags keyboard  # only the named roles
```

1. The wrapper takes the user lock and makes the binding, target, checkout and
   record checks of a home apply. It also requires the system prerequisites
   (the bootstrap packages, the installed `ansible-core` selected by `latest`
   in `mise.toml`, the Galaxy collections pinned in `requirements.yml`, Btrfs
   on `/` and `/home`, and
   snapper's `root` configuration) and a terminal on standard input, so a system
   apply never starts without someone there to type the password.
2. It captures the bound supplement, writes the user `pending` entry with scope
   `system` and the requested tags, and journals `begin`.
3. It runs `ansible-playbook -i localhost, site.yml --ask-become-pass`, with
   `--tags` when given, through `mise exec ansible-core --` from the checkout.
   `ANSIBLE_CONFIG` names the checkout's `ansible.cfg`, which reads collections
   only from `./collections` and runs modules with `/usr/bin/python3`. Every
   inherited `ANSIBLE_*` variable is dropped: `ANSIBLE_SKIP_TAGS=always` would
   skip the operation's root steps, and `ANSIBLE_COLLECTIONS_PATH` would replace
   the pinned collections. The operation (its ID, the capture path, the SHA-256
   of the capture's `manifest.json`, the target user, UID and home, and the
   checkout) travels as JSON in the one environment variable `SHAFFY_SYSTEM`,
   which the play reads with an `env` lookup. A lookup's result stays literal,
   whereas extra vars are templated, so a path containing `{{` would change.
4. After asserting an Arch-based host, the play's first tasks, tagged `always`,
   run the `operation` role. It copies `lib/shaffy/root_op.py` into the
   root-owned `/var/lib/shaffy/ops/<op>/` (0700) and runs that copy with
   `/usr/bin/python3 -I`. Holding the root lock, under umask 077, the root step:
   - refuses while any target's root record holds another operation;
   - checks the root record's target against the operation's;
   - copies the capture into `ops/<op>/capture/`, regular files and directories
     only and never following a link, then verifies the manifest against the
     digest the wrapper passed and every file against the manifest;
   - checks the root record's binding against the copied selection;
   - writes `settings.play.json` (0600): the captured settings, with the
     fragment paths (`overrides`) and the `references` list moved inside the
     root copy;
   - creates snapper's `home` configuration on first use, repairs its settings
     if they drifted, and adds the target user to it;
   - takes `pre` snapshots of `/` (configuration `root`) and `/home`
     (configuration `home`) before root `pending` exists, so rolling `/` back
     to its pre snapshot restores records that do not hold the operation;
   - sets root `pending`, writing the record before its journal, and journals
     `phase-start`.
5. The `settings` role, a dependency of every role that reads settings, with
   all its tasks tagged `always`, reads `settings.play.json` with `slurp` under
   become and sets `shaffy` with `b64decode | from_json`, under `no_log`. Module
   results stay literal, so a value shaped like Jinja is never evaluated.
   Settings never reach the play through `-e @file`, inline JSON extra vars,
   `vars_files`, `include_vars`, host or group variables or a YAML inventory,
   which all evaluate Jinja in string values.
6. The roles run in the order of `site.yml`.
7. The last task, tagged `always`, takes the `post` snapshots paired with the
   latest `pre` ones, journals `phase-done`, records the phase and
   `last_completed_op`, clears root `pending`, journals `complete` and prunes
   root copies.
8. When `ansible-playbook` succeeds and the root record shows the operation
   complete, the wrapper sets `system.bound` and `last_completed_op`, clears its
   `pending` entry and journals `complete`. If the checkout changed during the
   play, it journals `source-changed` and asks for another system apply. A
   failed play journals `system-failed` and leaves the operation unfinished.

Each routine role carries one fixed tag, its own name: `packages`, `desktop`,
`desktop_apps`, `workflows`, `writing`, `sync`, `home_backup`, `kde_connect`, `localsend`,
`tailnet`, `login`, `browser` and `keyboard`. `--tags` takes a comma-separated
list of them; the operation steps and the settings role run with every
selection. The wrapper never passes `--skip-tags`, and never runs the play in
check mode. The second play, `records.yml`, runs only the root step that abandons an
operation or moves the root binding.

Scripts from the checkout run as the target user. The one program root runs
from the checkout, `root_op.py`, runs from its copy in the operation directory,
and packages built from reviewed sources are built by the target user and
installed from a checked copy there. Root keeps its copies of the ten newest
completed system operations under `/var/lib/shaffy/ops/`, and never prunes the
copy of an unfinished or abandoned one.

## Snapshots

Shaffy OS needs Btrfs on `/` and `/home` and snapper with a `root`
configuration. The first system apply creates the `home` configuration with
`SYNC_ACL=yes` (so the user can read `.snapshots`), `TIMELINE_CREATE=no`,
`NUMBER_CLEANUP=yes` and `NUMBER_LIMIT=10`, and lists each target user in
`ALLOW_USERS`. Until it exists, a home apply refuses and names
`./bin/shaffy apply --system`.

Every pair Shaffy OS takes uses snapper's number cleanup and carries
`important=yes`, so frequent ordinary pairs, such as those of package
transactions, do not push it out first. A home apply takes one pair of the
`home` configuration; a system apply takes one pair each of `root` and `home`.
The home apply also keeps copies of what it changes in `runs/<op>/before/`.
`scripts/move-zotero-profile.py --apply`, which moves personal data outside an
apply, takes a `home` pair of its own around that step and refuses without one.

Recover selected files from a pair, never all of `/home`, which would also undo
unrelated changes:

```sh
snapper -c home status PRE..POST            # what the operation changed
snapper -c home undochange PRE..POST PATH   # restore one path from the pre snapshot
```

Tests replace snapper with a test adapter, which a home apply refuses for the
account's real home and a system apply refuses outside a container.

## Recovery

`./bin/shaffy status` reports the binding, the checkout, the target, any
unfinished operation with its snapshots and copies, the root record's state and
any disagreement, each integration in the ledger and, when nothing is
unfinished, what the next apply would refuse, change or defer for the
integrations. It also reports chezmoi's state file, the five latest journal
events, unreadable journal lines and the home's drift (`chezmoi status`,
skipped with `--no-drift`). It names the command for each situation:

| Situation | Meaning | Action |
| --- | --- | --- |
| Home operation unfinished, no `phase-start` | Nothing was written. | `apply --resume` clears it. |
| Home operation with `phase-start` or `resume-start` but no `phase-done` | The home may be partly written; `status` names the pre snapshot and the copies. | `apply --resume` reruns the phase from the retained capture, in the checkout the operation began in, after the same preflight. It takes a new pre snapshot and copies into `before-resume-N/`, journals `resume-start`, and clears `pending` only after `phase-done`. A refused resume journals `resume-refused` and writes nothing. |
| Home operation whose `phase-done` is journaled | It finished before its record was updated. | `apply --resume` records the completion. |
| Home operation without a readable `begin`, with a damaged or missing capture, or with unreadable journal lines of its own | It cannot be resumed safely. | `apply --abandon` journals `abandoned` with the snapshots and copies to review, clears `pending` and deletes nothing. |
| System operation that root never took | Root wrote nothing. | `apply --resume` clears the user side. |
| System operation that root holds | The system may be partly changed; the pre snapshots are in the root journal. | `apply --resume` reruns the play from the retained capture with the tags it began with; root verifies its copy again and takes new pre snapshots. `apply --abandon` clears root `pending` through `records.yml`, journals the pre snapshots to review and deletes nothing. |
| Root finished, user side unfinished | Only the wrapper's last step is missing. | `apply --resume` or `--abandon` completes it. |
| The root journal shows the operation started, but the root record no longer holds it | `/` was probably rolled back. | `apply --resume` refuses. Review the system against the pre snapshots, then `apply --abandon` clears the user side. |
| Root holds an operation the user record does not | `/` was rolled back to a moment root held it, or the user record was lost with a restored home. Every apply refuses. | `apply --system --abandon`, which works from the root record alone. `attach --adopt-system-record` refuses until then. |
| An integration left changing by an abandoned operation | Its ledger entry still carries that operation's transition. | `integration NAME adopt` if it is active and wanted, or `integration NAME release`. |

Anything else refuses and explains; nothing guesses. After an abandoned
operation, compare the home with its pre snapshot (`snapper -c home status
PRE..0` compares it with the current home) and the copies before applying again.

## Locks

- **User lock.** A non-blocking `flock` on `~/.local/state/shaffy/lock`, held
  for the whole of every apply, resume or abandon, `supplement attach` and
  `rebind`, and `integration`. A second command fails at
  once: "another Shaffy OS operation is running for USER".
- **Root lock.** A non-blocking `flock` on `/var/lib/shaffy/lock`, held by each
  root step.
- **System lock.** Root `pending` itself: a root step refuses to begin while any
  target's root record holds another operation, which serialises system
  operations across accounts.
- **chezmoi's state lock** serialises chezmoi commands on one state file. The
  preflight's `chezmoi status` takes only a shared lock, so a concurrent chezmoi
  command shows up at the apply step, as described above.

## Guards against direct changes

A direct chezmoi command that changes the home would bypass the capture, the
snapshots, the copies and the records. Three layers apply one rule: `apply`,
`update`, `destroy`, `purge` and `init` are refused, and so is `edit` when it
would apply. Flags are read as chezmoi's flag parser reads them: `--apply`,
`--watch` and `-a` count in every form it accepts, such as `--apply=1`,
`-a=true` or a cluster like `-va`; only an explicit false value such as
`--apply=false` or `-a=0` leaves the flag off. Arguments after `--` count too,
because chezmoi takes `--` as the value of a flag that expects one. Reading
commands (`diff`, `status`, `cat`, `verify`, `managed`, and `edit` without
applying) keep working and read the live settings.

1. **Source templates.** `home/.chezmoiexternal.toml.tmpl` and
   `home/.chezmoiignore.tmpl` start with the shared
   `home/.chezmoitemplates/guard`. chezmoi evaluates them for every command that
   reads the source state, including runs without the repository's
   `chezmoi.toml`, which skip its hooks. This layer also refuses `edit` when the
   user's own chezmoi configuration sets `edit.apply` or `edit.watch`.
2. **`home/.chezmoi.toml.tmpl`** fails every `chezmoi init`. init renders it
   before it applies or purges, and `init --purge` never reads
   `.chezmoiignore`, so without this layer it would delete the whole checkout,
   `.git` included. Nothing uses `chezmoi init` with this source: the tracked
   `chezmoi.toml` is not generated from the template and sets
   `warnings.configFileTemplateHasChanged = false`.
3. **Hooks.** The tracked `chezmoi.toml` has a pre-hook for `apply`, `edit`,
   `update`, `init`, `destroy` and `purge`. Each runs the checkout's own
   `bin/shaffy hook` through `sh`, found as `$CHEZMOI_SOURCE_DIR/..`, because
   chezmoi neither splits `command` nor runs hooks from the source directory.
   The hook reads chezmoi's argument vector from `/proc` when it matches
   `$CHEZMOI_ARGS`, which joins arguments with spaces, so an argument containing
   a space is read intact.

Only `SHAFFY_OP_MODE=apply`, set by the wrapper, passes the template layer and
the hooks; `init` is refused regardless. The render sandbox passes the template
layer only while the token file named by `SHAFFY_SANDBOX_TOKEN` exists; the
hooks never honour the sandbox, which uses a configuration of its own without
hooks. A hand-set `SHAFFY_OP_MODE=apply` passes both, but the loader then
refuses unless `SHAFFY_OP` and `SHAFFY_OP_CAPTURE` name the unfinished operation
on record, so a command that reads the source state fails there.
`./bin/shaffy check` refuses a tracked `chezmoi.toml` that lacks one of the
hooks or sets `[edit] apply` or `watch`. Hooks only refuse: record and ledger
writes happen in the wrapper, because post-hooks also run on `--dry-run` and do
not run after a failure.

What stays unguarded:

- `chezmoi purge`, and the `git pull` that `chezmoi update` runs, do not read
  the source first, so only the hooks stop them. Run chezmoi against this source
  only with the tracked configuration:
  `chezmoi --config chezmoi.toml --source home …`. An unguarded purge deletes
  the whole checkout, `.git` included, but leaves the home alone.
- `chezmoi state` is guarded by nothing. It changes only chezmoi's record of
  what it wrote, not the home, but an edited or deleted entry changes how
  chezmoi judges drift.
- Commands that write the source rather than the home, such as `add`, `re-add`
  and `forget`, are not refused. They change the public checkout, and adding a
  deployed file back into `home/` can copy personal values into it; personal
  values belong in the supplement.
- The guards prevent accidents. They are not a security boundary against
  someone who controls the account and the source.

## Ownership ledger

Optional integrations are listed in `defaults/integrations.yml` (`backup`,
`nextcloud`, `reaper`, `ssh`, `syncthing`, `zettlr` and `zotero`).
Each has a scope, the chezmoi targets that render only while it is managed, and
the activation records that switching it on creates. A supplement requests one
with `integrations: {NAME: managed | disabled | released}`; one it does not name
stays unmanaged. `.chezmoiignore.tmpl` ignores the targets of every integration
that is not managed, or that the operation defers, so those targets are left
alone and never removed.

The ledger, `~/.local/state/shaffy/ledger.json` (0600), records what this
installation owns. It is keyed by integration name, never by `supplement_id`,
so a rebind keeps ownership. Only the wrapper writes it, under the user lock
and with the records' atomic write, and it holds no settings values:

```json
{"ledger_version": 1,
 "target": {"uid": 1000, "home": "<home>"},
 "integrations": {
   "syncthing": {
     "class": "integration", "scope": "home", "state": "managed",
     "adopted": false,
     "activation": [
       {"kind": "wants-link", "unit": "syncthing.service",
        "link": "~/.config/systemd/user/default.target.wants/syncthing.service"}],
     "created_by": "<op>", "changed_by": "<op>", "changed_at": "<utc>",
     "transition": null}}}
```

Every catalogued integration has home scope, so the ledger lives beside the
user record. The catalogue uses three kinds of activation record:

- `wants-link`: a user unit enabled by a `.wants` symlink that chezmoi manages,
  so a fresh apply enables it without a session bus;
- `autostart`: a desktop entry under `~/.config/autostart/`;
- `config-key`: keys the framework sets in a file the application writes. These
  are recorded for reference only; the file belongs to the application, and
  disabling leaves those keys as they are.

The catalogue format also accepts `unit` and `command` records, but no
integration uses them and a home apply does not reverse them.

Before rendering, every home apply compares the settings with the ledger;
`diff` and `status` print the same comparison:

| Settings and ledger | Result |
| --- | --- |
| `managed`, not in the ledger, nothing active | The apply renders its targets and records the activation. |
| `managed`, not in the ledger, activation already present | Refused until `./bin/shaffy integration NAME adopt` records it. |
| `managed`, and its `activate_requires` does not hold yet | Deferred: this apply renders it as not managed, records nothing and says why. |
| `managed`, but the catalogue changed its activation | The new activation is recorded. |
| `disabled`, managed in the ledger | Its targets become ignored; the apply copies, then removes, exactly the recorded wants links and autostart entries, and keeps configuration and data. |
| `released`, in the ledger | The entry is removed; targets, activation and data stay as they are. |
| Absent from the settings, managed in the ledger | Refused until it is set to `disabled` or `released`. |
| In the ledger, gone from the catalogue | Refused until `integration NAME release`. |
| Still carrying another operation's transition | Refused until `integration NAME adopt` or `release`. |

A transition happens inside a home operation. The apply first writes each
entry's `transition` (`{op, to, remaining}`) and journals `ledger-intent`. After
chezmoi has applied and verified the home, it removes the `remaining` records
one at a time, rewriting the ledger after each (a link that is already gone
counts as removed), then writes the final state with `transition: null` and
journals `ledger-done`. An entry left with a transition belongs to the
unfinished operation: `--resume` repeats it, and after `--abandon` it waits for
`integration NAME adopt` or `release`.

`integration NAME adopt` records activation already present as `managed` with
`adopted: true`. It refuses while an operation is unfinished, when nothing of
the integration is active, when it is already managed, and while its
`adopt_requires` does not hold (or, when only part of its activation is
present, its `activate_requires`). `integration NAME release` removes the entry
and changes nothing else.

## Absence and removal

- A setting that is always managed renders its public default when absent.
- A home fragment (every `overrides.*` except `keyboard_hwdb`) renders into an
  `empty_` target, so removing it from the settings leaves an empty file where
  chezmoi would otherwise delete the target. Without
  `overrides.keyboard_hwdb`, the `keyboard` role removes the hardware database
  file it installed. A fragment or asset the settings name but the supplement
  lacks fails validation.
- A `create_` target, such as Noctalia's `99-runtime.toml`, is written once and
  then belongs to the application; an apply never overwrites it.
- `packages.omit` never uninstalls. Packages are removed only because they are
  listed, in the supplement's `packages.remove` or in the public
  `defaults/packages/retired.yml`.
- MCP entries are merged by name into the AI clients: Claude Code, Claude
  Desktop and Codex. Setting `ai.mcp_servers.NAME.enabled: false` removes that
  entry; removing a name from the supplement alone preserves an existing entry
  or restores its public default. Unrelated entries and client settings remain
  untouched.
- Framework-owned files are retired through `home/.chezmoiremove`, never
  directories that hold user data.
- Databases, credentials and personal folders are never deleted by an apply.

## The render sandbox and check

`./bin/shaffy check` validates without applying anything:

- the Python modules it needs are installed, found without importing them;
- the user record, if there is one, belongs to this user;
- the tracked `chezmoi.toml` keeps its hooks, leaves `[edit] apply` and `watch`
  unset, and makes `chezmoi dump-config` report
  `~/.local/state/shaffy/chezmoistate.boltdb`;
- the selected settings load and validate;
- assets have no conflict in the real home, the desktop key's build tools are
  installed, and a missing Git signing key is noted;
- a capture of the selection, taken as an apply would take it, renders in the
  sandbox;
- Noctalia keys that a managed fragment sets and a runtime file overrides are
  listed;
- system prerequisites are reported without failing the check, because the home
  does not need them.

`--no-render` stops after the settings and the conflict checks, and still
reports the system prerequisites.

The render runs `lib/shaffy/render_inside.py` in a bubblewrap sandbox. The host
root is read-only; `/home`, `/run`, `/tmp`, `/var/tmp` and `/dev/shm` are fresh
tmpfs mounts that hold only the synthetic home and the render's own
directories; `/dev` and `/proc` are the sandbox's own; every namespace is
unshared, the network included; the environment is cleared and `PATH` pinned;
and the user is a synthetic `tester` (UID 4242) on `synthetic-host`. The
supplement is mounted read-only, and chezmoi uses a configuration and a state
file of its own, without hooks. Inside, chezmoi applies the source twice, and
the second apply must change nothing. Then `chezmoi verify` must pass, every
asset's target must exist, and the rendered home must pass `niri validate`,
`noctalia config validate` (where only warnings about plugins, which Noctalia
fetches at runtime, are allowed), `fish --no-execute` for every Fish file, the
tmux configuration check, and a parse of every TOML, JSON and YAML file.

A passing check proves that the settings produce a valid, convergent home for a
synthetic user. Beyond the conflict checks above, it does not compare the real
home with the settings (`./bin/shaffy diff` and `status` do), run the system
roles, start services, reach the network or a session bus, or fetch Noctalia
plugins; [the VM validation](../tests/vm/README.md) does those in a freshly
installed CachyOS guest.

CI installs the declared mise controller and Galaxy collections before the
self-test, so playbook syntax and settings transport are checked there too.
A separate disposable-container job exercises real package operations, the
verified AUR build route, second-apply convergence and recovery between root
and user record writes. It uses the test snapshot adapter; the VM validation
still supplies evidence for real snapper, boot, graphical sessions and hardware.
`chezmoi --destination` alone would not isolate the render, because
templates and helpers could still reach the real home. `SHAFFY_RENDER_DIRECT=1` renders without bubblewrap only inside a
disposable container whose account holds no binding, and never for the
preflight of a real apply.
