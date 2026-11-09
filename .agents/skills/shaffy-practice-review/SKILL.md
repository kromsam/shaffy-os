---
name: shaffy-practice-review
description: Write or review a Shaffy OS change by the good practices of the tools it touches - the Red Hat CoP Automation Good Practices and the Zen of Ansible for roles, chezmoi's user guide for the home, the Google Shell Style Guide for scripts, PEP 8 for Python, and hardened GitHub workflows. Covers what needs judgment; the linters enforce the rest. Use when writing or changing roles, home templates, scripts, tests or workflows, and before committing such a change.
---

# Shaffy OS practice review

STYLE.md states the conventions and `./bin/shaffy lint` and the self-test
enforce them: read STYLE.md first and do not repeat its checks. This skill is
what no tool can check. Paths below are in the Shaffy OS checkout; outside it,
the `shaffy-os` skill names the checkout, and a supplement's scripts, tests and
workflows follow the same practices.

## Ansible

The roles follow the Red Hat CoP
[Automation Good Practices](https://redhat-cop.github.io/automation-good-practices/)
and their guiding principle, the
[Zen of Ansible](https://www.redhat.com/en/blog/the-zen-of-ansible). Here it
means:

- **Playbooks are not for programming.** A role states the outcome in modules;
  logic that needs data structures or several steps is a script in `scripts/`
  that takes `--check` or `--apply` from `ansible_check_mode`, as
  `configure-charge-limit.py` does. Long Jinja that builds data is the same
  smell.
- **Clear, concise, simple.** One role per function with one guaranteed
  outcome; a long task file splits into `tasks/<part>.yml` files that
  `main.yml` imports. Its inputs are `defaults/main.yml`, stated in
  `meta/argument_specs.yml`; an input with no sensible default is required
  there, never given a made-up one.
- **Declarative and idempotent.** A second apply changes nothing and reports
  nothing. `changed_when` tells the truth; a task that only reads has
  `changed_when: false` and `check_mode: false`. A role works in check mode.
- **A module before a command, a command before a shell.** Above every command
  or `lineinfile`, a comment says why no module does it. A configuration file is
  a template; `copy` is for static, binary or remote files, and `content:` for
  data that must stay literal, such as the supplement's keyboard map.
- **Handlers for restarts and reloads.** One that must happen now, because an
  interrupted apply would leave the change unloaded, runs inline with
  `# noqa: no-handler` and that reason.
- **The smallest scope for a variable.** Task or block `vars:` before role
  vars; `set_fact` only for a fact a later role needs, never over a default.
  Give a module the whole list (`pacman: name: [...]`), not a loop.
- **Settings stay data.** Values from the supplement reach a play only through
  the operation's verified copy (the settings role), are never templated as
  Jinja, and tasks that read them or a token carry `no_log: true`.
- **Tags run whole things.** A tag is a role's name; no tag runs half a change.
- **User experience first.** A failing assert's `fail_msg` tells the person
  what to do next, as the preflight tasks do.

## chezmoi

The home follows chezmoi's
[user guide](https://www.chezmoi.io/user-guide/manage-machine-to-machine-differences/)
and its [design notes](https://www.chezmoi.io/user-guide/frequently-asked-questions/design/):

- **The source is the truth.** Write `home/` by hand; preview with
  `./bin/shaffy diff PATH`; `./bin/shaffy check` renders and validates it.
  Never `chezmoi add`, `re-add` or a merge that takes the destination's side.
- **Differences between machines** come from the settings through the
  `shaffy.json` template; a file present on some machines only is
  `.chezmoiignore`'s ("ignore unless"); shared text is a template in
  `.chezmoitemplates/`.
- **The name says what the target is:** `private_` for a file only its owner
  reads, `executable_`, `symlink_`, `create_` for a file the application owns
  after the first write, `modify_` for one it shares (merge, never overwrite),
  `empty_` for one that is empty on purpose. A template that renders empty
  removes its target.
- **Templates:** `{{-` and `-}}` trim whitespace; a value goes into a string
  with `quote`, `toJson` or `toToml`; secrets come from the password manager at
  run time, never from the source.
- **Nothing outside the home**, and no `run_` scripts: the system is Ansible's.

## Scripts and tests

- **Shell** ([Google Shell Style Guide](https://google.github.io/styleguide/shellguide.html)):
  bash with `set -euo pipefail`; every expansion quoted; arguments in arrays;
  `local` in functions; errors to stderr, prefixed with the script's name.
  Past about a hundred lines, or with data structures, it is Python.
- **Python** ([PEP 8](https://peps.python.org/pep-0008/)): the standard library
  and the system interpreter; `subprocess` with an argument list, never
  `shell=True`, and `check=` stated; validation at the boundary (command line,
  settings, files) with a message that says what to do.
- **Tests** assert what a user of the code can observe, are named for the
  behaviour (`test_a_missing_tool_fails_and_says_how_to_install_it`) and use
  synthetic values only.

## Workflows and documents

- **Workflows:** actions pinned to a commit with the version in a comment,
  `permissions: contents: read` unless a job needs more, checkouts with
  `persist-credentials: false`, and secrets never echoed.
- **Documents:** plain British English, sentence case; each fact in one place,
  the others linking to it.

Report each finding with its file and line, the practice, and what goes wrong
without it. Say which findings a tool could have caught, so it can become one.
