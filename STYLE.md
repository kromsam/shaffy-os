# Style

One tool is the authority for each language, and `./bin/shaffy lint` runs them
all (`--fix` lets the formatters rewrite). The formatters are the ones the
desktop's own LazyVim formats with, StyLua, shfmt, fish_indent, taplo and
markdownlint-cli2, so the editor and the check agree. JSON and YAML the editor
formats with its language servers; the check only lints them. Install the tools
in the checkout with `mise trust mise.dev.toml` and `MISE_ENV=dev mise install`.
The `shaffy-practice-review` skill in `.agents/skills/` adds the practices of
each tool that no linter can check.

| Language | Authority | Settings |
| --- | --- | --- |
| Python | ruff format and ruff check | `ruff.toml`: single quotes, 120 columns, PEP 8 and an explicit rule list, the system Python as target |
| Shell | shfmt and ShellCheck | `.editorconfig`: two spaces, indented `case` branches, operators first on a continued line; `.shellcheckrc`: `"${var}"` with braces and a default `case` branch, among ShellCheck's optional checks |
| Fish | fish_indent | |
| Lua | StyLua | `stylua.toml`: two spaces, 120 columns |
| Markdown | markdownlint-cli2 | `.markdownlint-cli2.yaml`: prose lines may be any length |
| Ansible | ansible-lint | `.ansible-lint`: the production profile and the opt-in rules for the practices below |
| YAML | yamllint | `.yamllint`: a `---` start, double quotes when quoted, lists indented under their key |
| GitHub workflows | actionlint, zizmor and yamllint | |
| TOML | taplo | `.taplo.toml`: 120 columns |
| JSON | `shaffy lint` itself | it parses, and keeps to `.editorconfig` |
| Every other text file | `shaffy lint` itself | `.editorconfig`'s `[*]`: UTF-8, LF, a final newline, no trailing spaces, no tab indentation; `--fix` mends all but the tabs |

Templates (`*.tmpl`, and `home/.chezmoitemplates/`) get no formatter, which
would not read their template syntax, but keep to `.editorconfig`. The sandboxed
render checks what they produce with each application's own validator where it
has one: Niri, Noctalia, Fish, tmux and desktop-file-validate. Files copied from
other projects, which `REUSE.toml` attributes to them, keep their upstream's
form. A rule is switched off for the whole repository only in its tool's
settings file, with the reason beside it. A finding that stays gets an inline
directive with its reason (`# noqa: B008 — …`,
`# shellcheck disable=SC2086 # …`), or, for a whole Ansible file, a line in
`.ansible-lint-ignore` under a comment that says why.

Beyond the tools:

- A script names the system interpreter: `#!/usr/bin/python3`, `#!/bin/bash` or
  `#!/bin/sh`, never `env`. The system Python has the packages Shaffy OS
  installs; `env python3` may find mise's. `shaffy lint` checks it, and
  `--fix` rewrites the shebang.
- Shell is for short glue. A script that grows past about a hundred lines, or
  needs data structures, is written in Python, as `restic-home` and
  `shaffy-desktop` are. Python stays on the standard library unless a package
  Shaffy OS installs anyway (PyYAML, jsonschema) does the job.
- Scripts are named verb first, lower case with hyphens: `install-`, `build-`,
  `configure-`, `inspect-`, `validate-`.
- Comments say why, not what. Prose is plain British English, and headings are
  in sentence case.

## Ansible

The roles follow the Red Hat Communities of Practice
[Automation Good Practices](https://redhat-cop.github.io/automation-good-practices/).
ansible-lint checks most of them; `tests/test_ansible_style.py` checks the rest.

- One role per function, one tag per role, named after it. `site.yml` lists the
  roles, between the operation's first and last steps.
- Every role has `meta/argument_specs.yml`: its purpose, and the inputs its
  `defaults/main.yml` declares. Ansible validates them before the role runs.
- Variables carry their role's name: `power_state_dir` for an input, and two
  leading underscores for everything internal, registered results included
  (`__power_reload_pending`). The play's shared settings, `shaffy` and
  `shaffy_*`, are the one exception (`.ansible-lint-ignore`).
- Values are looked up with brackets, `shaffy['settings']['power']`, never
  `shaffy.settings.power`, and facts as `ansible_facts['hostname']`, never
  `ansible_hostname` (`inject_facts_as_vars = False`).
- A condition that is a bare variable is cast, `when: __login_autologin | bool`;
  one that is an `and` of several is a list.
- Every task is named, in the imperative. A task in a file other than
  `main.yml` starts with that file's name: `preflight | Require ...`. Task
  files are named in snake_case.
- `changed_when` on every command, and a comment above it that says why no
  module does the job. A file a role ships is a template (`.j2`) or a static
  file under `files/`, and, where the format has comments, starts with a
  `Managed by Shaffy OS (the NAME role)` header.

Some of the guide's practices do not fit a desktop that one playbook applies to
one machine, and are left out deliberately:

- `site.yml` keeps Ansible's own name for the entry point, not a verb-noun one.
- Files that another program reads under its own name keep it, `.yaml`
  included: a supplement's `config.yaml`, `.markdownlint-cli2.yaml`,
  `.chezmoidata.yaml`. Ansible's files are `.yml`.
- The roles have no README each: `meta/argument_specs.yml` states the purpose
  and inputs, and the documents in `docs/` the behaviour.
- No Molecule: `tests/test_system_container.py` applies the package roles to a
  disposable CachyOS container, and the VM kit in `tests/vm` applies the whole
  system to an installed CachyOS; both check that a second apply changes
  nothing.
- `community.general.pacman`, not the generic `package` module: Shaffy OS runs
  on CachyOS alone.
- Commit messages are a plain sentence in the imperative, not Conventional
  Commits, and releases are dated (`vYYYY.MM.DD`), not semantic versions.

## chezmoi

The home follows chezmoi's own
[user guide](https://www.chezmoi.io/user-guide/manage-machine-to-machine-differences/).

- The source state is `home/`, and `./bin/shaffy` passes it with `--source`.
  It is the only way in, so there is no `.chezmoiroot`.
- `home/.chezmoiversion` names the oldest chezmoi Shaffy OS supports; chezmoi
  refuses to read the home with an older one.
- A value from the settings goes into a quoted string with `quote` (or
  `toJson` for JSON), never between hand-written quotes. A template that must
  not end in a newline trims it with `-}}` rather than leaving the file without
  one.
- Shared text is a template in `.chezmoitemplates/`, and whether a file is
  managed at all is `.chezmoiignore`'s.
- No `run_` scripts and nothing outside the home: the system is Ansible's.
- The source is written by hand, never with `chezmoi add` or `re-add`
  ([AGENTS.md](AGENTS.md#bringing-a-file-under-management)).
