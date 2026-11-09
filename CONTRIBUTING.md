# Contributing to Shaffy OS

Shaffy OS gets better when the people who use it bring back what they find: a
bug, a missing feature, or a setting from their supplement that everyone would
want. Agents help too: when an agent working on your machine finds one, it
drafts the issue or pull request for you to read and send
([bring it upstream](AGENTS.md#bring-it-upstream)).

[AGENTS.md](AGENTS.md) is the working contract for people and agents. Read it
before you change anything; this page is about getting a change here.

## Personal or public

Decide first where the change belongs
([AGENTS.md](AGENTS.md#decide-first-whether-the-value-is-personal)). A value
that identifies you, your machine or an account, or adapts the desktop to your
installation alone, stays in your supplement. A personal need that the
supplement has no setting for is a public change: propose the setting, not your
value.

## Report a bug

Open an issue with the **Bug report** form. Give the release
(`git describe --tags` in the checkout), what you did, what you expected and
what happened, with the relevant output of `./bin/shaffy check` and
`./bin/shaffy status`. Read that output before you paste it and remove names,
host names, addresses and paths that identify you. Never paste the output of
`./bin/shaffy diff` or a secret
([keep private output private](AGENTS.md#keep-private-output-private)).

A security problem is not an issue: follow [SECURITY.md](SECURITY.md).

## Propose a feature or a configuration

- **A feature**, such as a new default application, integration or setting:
  open an issue with the **Feature** form before you build it, so that the
  scope is agreed first. The default desktop is complete and opinionated
  ([product scope](AGENTS.md#product-scope)); a new default has to suit most
  people who install it.
- **A configuration** you have in your supplement and others would want: open
  an issue with the **Configuration** form. Show the setting, override or
  asset with your personal values replaced, and say whether it should become a
  default for everyone or a mechanism that any supplement can use.

## Make a change

1. Fork the repository and branch from `dev`.
2. Make the change in the source that owns it ([the rule](AGENTS.md#the-rule)),
   with synthetic values in tests and fixtures.
3. Install the development tools once (`mise trust mise.dev.toml`, then
   `MISE_ENV=dev mise install`) and run `./bin/shaffy lint` and
   `./bin/shaffy selftest`. CI also scans the history for secrets
   (`MISE_ENV=dev mise exec -- gitleaks git .`) and checks the licence
   information (`MISE_ENV=dev mise exec -- reuse lint`).
4. Try the change on your machine with `./bin/shaffy apply --checkout PATH`,
   run in your branch's checkout, and confirm that a second apply changes
   nothing. Your binding stays on your released checkout, and its next apply
   returns the machine to `main`.
5. Read your diff for personal values. With your supplement bound, the
   self-test fails on any value its denylist or identifying settings name, but
   it cannot recognise every one.
6. Open a pull request against `dev` and fill in its template.

The tests render the home with CachyOS's own packages (chezmoi, Niri,
Noctalia, Fish), so they run on a CachyOS machine. Without one, CI runs them
for your pull request in a CachyOS container, together with the system
operation tests.

The skills in `.agents/skills/` review a change the way a maintainer will:
`shaffy-change-review` for where it belongs and what it implies,
`shaffy-risk-review` for what it could destroy and `shaffy-lean-review` for what
can go ([agent tooling](AGENTS.md#agent-tooling)).

## Licence

Shaffy OS is MIT-licensed ([LICENSE](LICENSE)), and a contribution is made under
the same licence. A file taken from another project keeps its own licence:
annotate it in `REUSE.toml` and list it in `THIRD-PARTY.md`.
