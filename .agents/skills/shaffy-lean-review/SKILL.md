---
name: shaffy-lean-review
description: Review a Shaffy OS change or area for what can go - settings equal to the CachyOS baseline, a value kept in two places, transitions past their date, a new tool or language for one task, dead code and repeated documents. Use when asked to simplify, before a large change, and in periodic reviews.
---

# Shaffy OS lean review

The repository keeps only what produces Shaffy OS's differences from a fresh
CachyOS Niri/Noctalia install, with the structure those need (CLAUDE.md,
"Product scope"). Apply pstack's `principle-laziness-protocol` skill,
then look for:

- **The baseline.** Settings equal to what the installer ships: compare with
  `/etc/skel/.config/niri/` and `/etc/skel/.config/noctalia/`
  (`pacman -Ql cachyos-niri-noctalia`). An equal value kept on purpose has a
  comment that says why.
- **One place per value.** A version, path or name written in two places (code
  and a document, two scripts) drifts apart. Keep it in one and point there.
- **Transitions past their date.** `./bin/shaffy outdated` lists them; delete
  each with everything its comment names.
- **A new tool, language or dependency for one task.** Prefer the standard
  library, the glue that exists, or a tool Shaffy OS installs anyway.
- **Dead weight.** Functions nothing calls, options nobody sets, tests that
  check nothing relevant, documents that repeat each other.

Propose deletions first. Each finding names the file and line, what can go and
what keeps working without it.
