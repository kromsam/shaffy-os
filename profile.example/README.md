<!--
Copied from profile.example/ in Shaffy OS. Replace OWNER/REPO with this repository,
describe each profile in the table, fill in the year and the holder in LICENSE, and
delete this comment. docs/PROFILES.md in Shaffy OS describes the format and the check.
-->

# Shaffy OS profiles

Profiles for [Shaffy OS](https://github.com/kromsam/shaffy-os): small public bundles of
settings that a Shaffy OS installation vendors into its private supplement. Each folder
here with a `profile.yaml` is one profile, named after the folder.

| Profile | What it sets up |
| --- | --- |
| [example](example/README.md) | Installs one text file, `~/.local/share/example-profile/hello.txt`. |

## Use a profile

Run this in your Shaffy OS checkout, with your supplement bound:

```sh
./bin/shaffy profile add OWNER/REPO example
```

The command prints what the profile runs and installs. Read the vendored copy, list
`owner/example` under `profiles:` in your supplement's `config.yaml`, then check, commit
and apply, as
[Use a profile](https://github.com/kromsam/shaffy-os/blob/main/docs/PROFILES.md#use-a-profile)
describes.

## Contributing

Issues and pull requests are welcome. If a profile does something wrong for you, or lacks
something you need, open an issue or send a fix. In Shaffy OS,
`./bin/shaffy profile propose owner/NAME` turns your edits of a vendored copy into a patch
for this repository; restore the copy afterwards with `git restore`.

A need that no profile can express, such as a setting profiles may not share, is a change
to Shaffy OS: `./bin/shaffy profile propose owner/NAME --to shaffy` drafts that issue.

Before you open a pull request, run `./bin/shaffy profile check PATH` from a Shaffy OS
checkout. CI runs the same check on every push.

## Licence

[MIT](LICENSE).
