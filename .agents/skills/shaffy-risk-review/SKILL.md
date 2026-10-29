---
name: shaffy-risk-review
description: Review a Shaffy OS change for what it could destroy or leave half done - removals, package removals, integration transitions, root operations, resume and abandon, privileges and secrets. Use for changes to lib/shaffy/, roles/, home/.chezmoiremove, retired packages and integrations.
---

# Shaffy OS risk review

Shaffy OS never deletes user data, and every apply can be finished or given up
(docs/ARCHITECTURE.md). Trace the changed paths for the ways that breaks:

- **Removal.** A `home/.chezmoiremove` entry, a `packages.remove` or
  `retired.yml` package, an integration switched to `disabled`: does it touch
  only what Shaffy OS created? Application data, profiles and the user's files
  stay. Does the apply copy each target before it changes or removes it?
- **Root.** A role task that writes outside its own files, a written file
  without a header naming its source, a command without `changed_when`, root
  reading the user's files (system overrides are read into the settings
  instead).
- **Interruption.** If the operation stops right after this step, does
  `./bin/shaffy apply --resume` finish it from the capture, and does `--abandon`
  name what to review? Is each record written before the effect it describes,
  and each journal line appended whole?
- **Selection.** Does anything read the live settings during an operation
  instead of its capture, or fall back to the defaults when the binding is
  missing?
- **Privileges and secrets.** No secret in the supplement, the settings, a log
  or a copy; no new path to sudo; Docker without the docker group.
- **Tests that would miss it.** Name the defect each changed test should catch,
  and check that it would
  ([test behaviour, not implementation](../principle-test-behavior-not-implementation/SKILL.md)).

Report a demonstrated defect with its file and line, the trigger, the
consequence and the evidence. Report a concern with its assumption and how to
settle it.
