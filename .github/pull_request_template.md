<!-- markdownlint-disable-file MD041 -- a pull request body starts under its title -->
## What and why

<!-- What the change does for someone who installs Shaffy OS, and the issue it closes. -->

## Checks

- [ ] The pull request targets `dev`.
- [ ] `./bin/shaffy lint` and `./bin/shaffy selftest` pass.
- [ ] Tried with `./bin/shaffy apply --checkout PATH`, and a second apply changes nothing, or the change does not reach a machine.
- [ ] The diff holds no personal values; new settings are tested with synthetic ones.
- [ ] The documents say what the code now does, and a retirement carries `Remove after YYYY-MM-DD`.

## What a supplement must change

<!-- Nothing, or the settings a supplement has to add, rename or remove with this release. -->
