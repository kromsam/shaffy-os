---
name: shaffy-change-review
description: Review a Shaffy OS change for where it belongs and what it implies - the owning source, personal values, a missing supplement mechanism, a shareable setting that belongs in a profile, convergence, the CachyOS baseline, retirements and the documents. Use before committing a change and when asked to review one.
---

# Shaffy OS change review

Read AGENTS.md first; this review checks a change, to the checkout or to a
supplement, against it. Paths below are in the Shaffy OS checkout; outside it, the `shaffy-os` skill
names the checkout. For each changed file, ask:

1. **The owning source.** Is the change made in the source that owns it
   (`home/`, `roles/`, `defaults/`, the schema, the wrapper), never in the live
   system? Does it need a change on the running machine that the diff does not
   make through a source?
2. **Personal or public.** Does a value identify a person, a machine or an
   account, or adapt the desktop to one installation? Then it belongs in the
   supplement, never here, not even as an example or a default. Read every
   literal: the self-test's scanner cannot recognise them all.
3. **A missing mechanism.** A personal need without a supplement setting is a
   public change: the schema field, the template or role that reads it, a
   synthetic fixture under `tests/fixtures/supplements/` and a test.
4. **A shareable setting.** A supplement setting that names no person or
   machine and that others would want, but not everyone, belongs in a profile
   (docs/PROFILES.md): a fragment, an asset, an extra package, an editor extra,
   a plugin or an MCP server. Suggest moving it there ("Share yours"). A
   vendored copy under a supplement's `profiles/` that is applied or committed
   edited is never right: the change goes in `config.yaml` and the fix to the
   profile's author. The copy is edited only to draft that fix with
   `./bin/shaffy profile propose ID`, and restored in the same sitting. A need
   profiles cannot express is a Shaffy OS change
   (`./bin/shaffy profile propose [ID] --to shaffy`).
5. **Convergence.** Would a second apply change nothing? Look for commands
   without `changed_when`, generated timestamps, an order that varies, and files
   an application rewrites, which need a `modify_` script or an integration's
   config keys.
6. **The baseline.** On a fresh CachyOS Niri/Noctalia install, is this a
   difference Shaffy OS makes? A setting equal to the installer's
   (`/etc/skel/.config/`, from `cachyos-niri-noctalia`) or an already installed
   package adds nothing.
7. **Transitions.** Does it retire or migrate something? Then it carries
   `Remove after YYYY-MM-DD` (AGENTS.md, "Retirements and migrations").
8. **The documents.** The README's command table, the "as implemented"
   statements in docs/ARCHITECTURE.md, docs/SUPPLEMENT.md for a setting,
   docs/PROFILES.md for what a profile may set, and any
   document that repeats a value the code holds: do they still say what the
   code does?

Report each finding with its file and line, what is wrong, the consequence and
the evidence: the rule or the line that shows it. Say plainly when a check
found nothing.
