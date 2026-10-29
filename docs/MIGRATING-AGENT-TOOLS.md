# Retiring GBrain and Gstack

<!-- Remove after 2027-01-16, with scripts/archive-gbrain.py. -->

The home apply removes the retired GBrain launchers, proxy, service and MCP
entries. It preserves private application data and unrelated agent settings.
The workflow plugin for Claude Code and Codex is pstack
([workspace tools](WORKSPACE-TOOLS.md#pstack)), which the workspace installer
supplies; it never installs Garry Tan's Gstack.

## Prepare the sources

Keep changes in reviewed branches. Remove `ai.gbrain` and `integrations.gbrain`
from the private supplement; keep any desired `ai.pstack.enabled` value
([workspace tools](WORKSPACE-TOOLS.md#pstack) describes project opt-in).
The public `ai.mcp_servers.gbrain.enabled: false` marker removes a previously
configured server. Do not remove that marker merely because the integration is
no longer catalogued. Never copy private brain contents into this public repo.

Use pstack for debugging, review and PR creation. Preserve each project's
actual test and deployment gates. For PR consumers, explicitly supply the heading
and checkbox format they parse. Existing Lighthouse and dependency scanners stay
in their projects; a generic review skill is not an executable replacement for
those scanners. Historical reports can retain old tool names as provenance.

Read saved lessons against current code. Publish only verified engineering
lessons, with source links and prevention steps, in the project's own
documentation. Archive stale recommendations, personal profiles, telemetry and
session transcripts. Do not alter manuscripts while migrating engineering
tooling.

## Archive before cutover

The owner closes all agent clients, chooses a private directory outside every git
checkout, and runs the helper from this reviewed source:

```sh
python3 scripts/archive-gbrain.py --archive-dir "$PRIVATE_ARCHIVE"
python3 scripts/archive-gbrain.py --archive-dir "$PRIVATE_ARCHIVE" --apply
python3 scripts/archive-gbrain.py --archive-dir "$PRIVATE_ARCHIVE" --verify
```

The first command is a dry run. The applied helper pauses an active user service,
acquires the installed PGLite writer lock, snapshots private brain data, the memory
worktree (including git history), credentials and installed GBrain sources, and
restores the service's previous active state. It checks that copied directories
match their sources. A held database lock stops the operation without bypassing
another writer.

The raw snapshot is never opened by an engine. A separate disposable database
copy produces JSONL for every public database table plus readable pages, including
soft-deleted pages. Hashed source and slug identities prevent duplicate names from
overwriting each other. index.json records the original identities and row counts;
manifest.json records raw/export checksums. Any export failure leaves a partial
archive for inspection and prevents a success report. Retry into a new directory.

Keep the archive private: it includes credentials and personal material. Preserve
its manifest and copies on durable backup storage before retiring access. A
checksum verification confirms copy integrity, not that a full restored GBrain
service has been exercised. Keep the original data until a restore rehearsal and
normal backup cycle have succeeded. The helper never deletes it.

## Owner-approved cutover

Review the source diff and coordinated supplement change first. Existing ownership
must be released before applying a catalogue without that integration:

```sh
systemctl --user stop gbrain-mcp.service
./bin/shaffy integration gbrain release
```

If already released or never owned, skip the release. Install the reviewed source
changes in both the public and bound private checkouts, respecting the release
branch policy. Then use the normal source-owned path:

```sh
./bin/shaffy check
./bin/shaffy diff
./bin/shaffy apply --home
systemctl --user daemon-reload
bash scripts/install-workspace-tools.sh
./bin/shaffy diff
./bin/shaffy status
```

Check that the second diff is empty, both agent MCP lists omit GBrain, both native
plugin lists match the chosen global policy, and restarted opted-in sessions expose
pstack's skills. With opt-in, also check a work repository and a new repository:
pstack must be off. Native installation temporarily enables pstack, so keep clients
closed until the refresh helper has reapplied the source policy. Confirm
existing Zotero, workmux hooks, permissions and unrelated plugins still work.
The workspace refresh captures installed plugin versions in private state.

Verify that a generated PR body contains the project's literal Test plan heading
and checkbox syntax, and that the downstream verifier consumes it. Do not publish
a test PR merely to check formatting. Native plugin loading needs the owner's
running environment; passing config tests alone does not establish it.

Remove only an identified GarryTan Gstack installation. A system executable named
gstack may be GDB's unrelated stack-trace tool and must remain. Old ignored agent
folders and archived provenance do not establish an active installation. Private
data, credentials and the archived source can be retained without exposing them
to agents; do not prune them during this cutover.

## Recovery

Stop agent clients before a restore. Revert the coordinated source/supplement
change through the normal release process, re-adopt the legacy integration under
the previous catalogue, and apply those sources. Restore a verified raw snapshot
only while the service is stopped, keeping any newer live data in a second private
backup. Preserve paths, symlinks and modes; use the archived installed source and
original runtime, without an upstream database upgrade. Restart the old service
only after its data and scoped client credentials are restored.

Do not restore a frozen database over newer work. Do not initialize a new brain
to silence a missing-data error. `ai.pstack.enabled: false` in the supplement
turns pstack off and keeps it installed.
