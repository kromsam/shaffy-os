# Style

One tool is the authority for each language, and `./bin/shaffy lint` runs them
all (`--fix` lets the formatters rewrite). They are the formatters the desktop's
own LazyVim uses, so the editor and the check agree. Install them in the
checkout with `mise trust mise.dev.toml` and `MISE_ENV=dev mise install`.

| Language | Authority | Settings |
| --- | --- | --- |
| Python | ruff format and ruff check | `ruff.toml`: single quotes, 120 columns, an explicit rule list |
| Shell | shfmt and ShellCheck | `.editorconfig`: two spaces, indented `case` branches |
| Fish | fish_indent | |
| Lua | StyLua | `stylua.toml`: two spaces, 120 columns |
| Markdown | markdownlint-cli2 | `.markdownlint-cli2.yaml`: prose lines may be any length |
| Ansible | ansible-lint | `.ansible-lint`: the shared profile, fully qualified modules |
| GitHub workflows | actionlint | |

Templates (`*.tmpl`) are not formatted; the sandboxed render checks what they
produce. Files copied from other projects, which `REUSE.toml` attributes to
them, keep their upstream's form. A finding that stays gets an inline directive
with its reason (`# noqa: B008 — …`, `# shellcheck disable=SC2086 # …`), never a
rule switched off for the whole repository.

Beyond the tools:

- A script names the system interpreter: `#!/usr/bin/python3`, `#!/bin/bash` or
  `#!/bin/sh`, never `env`. The system Python has the packages Shaffy OS
  installs; `env python3` may find mise's. The self-test checks it.
- Shell is for short glue. A script that grows past about a hundred lines, or
  needs data structures, is written in Python, as `restic-home` and
  `shaffy-desktop` are. Python stays on the standard library unless a package
  Shaffy OS installs anyway (PyYAML, jsonschema) does the job.
- Scripts are named verb first, lower case with hyphens: `install-`, `build-`,
  `configure-`, `inspect-`, `validate-`.
- Comments say why, not what. Prose is plain British English, and headings are
  in sentence case.
- Ansible: one tag per role, role-prefixed variables, `changed_when` on every
  command, and a header naming the source in every file a role writes.
