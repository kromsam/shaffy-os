# Security

Shaffy OS runs Ansible roles as root, builds AUR packages, configures backups
and the firewall, and writes the configuration of AI agents. A flaw in any of
these can expose a machine, so report it privately.

## Report a vulnerability

Use GitHub's private vulnerability reporting: on the repository, open
**Security**, then **Report a vulnerability**. Describe the problem, the release
it affects (`git describe --tags`), how to reproduce it and what an attacker
could do with it. Leave out your own secrets and personal values.

Do not open a public issue, pull request or discussion about it before a fix
is released. The maintainers acknowledge a report, keep you informed and credit
you in the release notes unless you prefer otherwise.

## Supported releases

Only the newest release on `main` receives fixes. Update with
[Updating](docs/UPDATING.md) before you report, and say if the problem is gone.

## What is in scope

Shaffy OS's own code and configuration: `./bin/shaffy` and `lib/shaffy/`, the
roles, the home source, the scripts and the CI workflows. A flaw in an
application or package that Shaffy OS installs belongs with that project; if
Shaffy OS configures it unsafely, that is in scope.
