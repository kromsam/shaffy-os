# Profiles

A profile is a small public bundle of settings that someone shares from their
own setup: a writing setup with Pandoc filters and a citation style, a
language's runtime with its editor extra, or the key map of one keyboard model.
Authors publish profiles in repositories of their own. You vendor a profile
into your supplement, which keeps a reviewed copy of it at one commit, and your
supplement's `config.yaml` always wins over it.

A profile adds things: fragments, files, packages, editor extras, Claude Code
plugins and MCP servers. It never sets a value the machine has only one of, and
nothing that names a person or a machine. When a profile is wrong or lacks
something, the fix goes back to its author, so everyone who uses it gets it
([fix it upstream](#fix-it-upstream)).

## Use a profile

Run these steps in your Shaffy OS checkout, with your supplement bound. The
example vendors the `writing` profile from the repository `alice/shaffy-os-profiles`.

1. Vendor the profile:

   ```sh
   ./bin/shaffy profile add alice/shaffy-os-profiles writing
   ```

   The command fetches the repository's default branch, copies the `writing`
   folder at that commit into your supplement's `profiles/alice/writing/`, and
   records the source, the commit and every file's SHA-256 in
   `profiles/lock.json`. It prints what the profile asks of the machine, the
   entries that run code or reach the network first, among them every asset
   outside the folders for fonts, icons, themes and pictures. `--ref` names a
   branch, a
   tag or a commit to follow instead. A source is `OWNER/REPO` on GitHub, or
   the `https` URL of any Git repository.
2. Read what it vendored. The copy is code that will run on your machine:

   ```sh
   cd ~/Projects/my-supplement
   git add --intent-to-add profiles && git diff -- profiles
   ```

3. List the profile in `config.yaml`. Profiles apply in the order of the list,
   and `config.yaml` itself comes last:

   ```yaml
   profiles:
     - alice/writing
   ```

4. Check and preview the result, then commit and apply:

   ```sh
   ./bin/shaffy check
   ./bin/shaffy diff
   git -C ~/Projects/my-supplement add profiles config.yaml
   git -C ~/Projects/my-supplement commit -m "Use alice/writing"
   ./bin/shaffy apply --home
   ```

   A profile's `packages.extra` installs at the next system apply
   (`./bin/shaffy apply --system --tags packages`), and its keyboard map with
   `--tags keyboard`.

Nothing changes on the machine before you list the profile and apply. Every
load (`check`, `data`, `diff` and `apply`) verifies the vendored copy against
`profiles/lock.json` and refuses a copy that differs from it.
`./bin/shaffy data` shows which layer set each asset and each value, and
`./bin/shaffy status` lists the profiles in use with their commits.

Nothing is fetched during a check, a render or an apply. Only `profile add` and
`profile update` read the author's repository, and only `./bin/shaffy profile`
writes the `profiles/` folder.

## How layers combine

The settings are layers, applied in this order: the public defaults, each
listed profile in the order of `profiles:`, and the supplement's `config.yaml`,
which wins.

- **A single value**, such as `desktop.browser`, comes from the last layer that
  sets it. Profiles set no such values, so this is `config.yaml` over the
  defaults, as without profiles.
- **`packages.extra` and `lazyvim.extras`** gather the items of every layer, in
  layer order.
- **A named entry**, an MCP server under `ai.mcp_servers` or a plugin under
  `ai.claude_plugins`, builds on the same entry of the layers before it. An MCP
  server's fields merge one by one. When two profiles give one entry different
  values, every load stops and names both, until `config.yaml` writes that
  entry. Its entry then replaces both.
- **An asset target** that two profiles give different files is the same
  error, until `config.yaml` sets the target. The entry in `config.yaml` for a
  target replaces a profile's.
- **A fragment** stays a file of its own. Each tool loads the profiles'
  fragments after the public configuration and before the supplement's own, in
  list order, so a later file wins a key that both set:

  | Fragment | Installed as | Loaded |
  | --- | --- | --- |
  | `overrides.niri` | `~/.config/niri/profiles/NN-OWNER-NAME.kdl` | `profiles.kdl` includes it before the supplement's `user.kdl` |
  | `overrides.noctalia` | `~/.config/noctalia/96-profile-NN-OWNER-NAME.toml` | after the public `90` to `95` files, before `97-supplement.toml` |
  | `overrides.fish` | `~/.config/fish/profiles/NN-OWNER-NAME.fish` | `profiles.fish` sources it before `supplement.fish` |
  | `overrides.alacritty` | `~/.config/alacritty/profiles/NN-OWNER-NAME.toml` | `profiles.toml` imports it before `supplement.toml` |
  | `overrides.mise` | `~/.config/mise/conf.d/80-profile-NN-OWNER-NAME.toml` | before `90-supplement.toml` |
  | `overrides.keyboard_hwdb` | a part of `/etc/udev/hwdb.d/90-shaffy-keyboard.hwdb` | before the supplement's part, which udev reads last |

  `NN` is the profile's place in the list. `./bin/shaffy check` notes a key of
  a Noctalia, Alacritty or mise fragment, or a keyboard match, that two
  profiles both set differently, and names both authors.

A profile can need an integration, such as `zotero`, in its `requires:`. The
profile does not change the integration's state. `config.yaml` must set it to
`managed`, and a load names the line to add until it does.

A profile's extra package that a public group already installs is a note, not
an error. `packages.omit` in `config.yaml` leaves out a profile's extra package
without uninstalling it. To uninstall it, list it in both `packages.omit` and
`packages.remove`.

## What a profile may set

A profile's `profile.yaml` has `schema_version: 1`, a one-line `description`,
an optional `minimum_release` and an optional `requires`, and these settings,
in the same shape as in `config.yaml` ([the supplement](SUPPLEMENT.md#field-reference)):

- `overrides`: the six native fragments, each a file in the profile's folder;
- `assets`: files copied into the home, each a file in the profile's folder;
- `packages.extra`;
- `lazyvim.extras`;
- `ai.mcp_servers` and `ai.claude_plugins`.

`schema/profile.schema.json` is the schema, for editors and for the loader.
The rest stays personal, and a load that finds it in a profile names the
reason and both ways out:

- Values that name a person, a machine or an account: `supplement_id`,
  `identity`, `tailnet`, `kde_connect`, `localsend`, `backup` and `folders`.
- Values the machine has only one of, which two profiles would fight over:
  `desktop.browser`, `desktop.keyboard`, `desktop.displays`,
  `desktop.wallpaper`, `desktop.lock_screen`, `desktop.boot_splash` and
  `ai.pstack`.
- Changes that adapt or remove what the installation has: `packages.omit`,
  `packages.remove`, `integrations`, `dotfiles`, and the root sections `login`
  and `power`.
- `profiles`: a profile does not pull in other profiles.

Some files are refused as well. A profile may not write under `~/.ssh/`,
`~/.gnupg/`, `~/.pki/`, `~/.password-store/`, `~/.local/share/keyrings/`,
`~/.config/chezmoi/` or `~/.local/state/shaffy/`. A profile shares
preferences, never access or the state of the tools that manage the home. No
asset, in a profile or in `config.yaml`, may lie where profile fragments go
(`~/.config/niri/profiles/*.kdl`, `~/.config/fish/profiles/*.fish`,
`~/.config/alacritty/profiles/*.toml`, `~/.config/noctalia/96-profile-*.toml`
and `~/.config/mise/conf.d/80-profile-*.toml`), since an apply deletes a file
there that no profile sets; set the file as an `overrides` fragment instead.
A keyboard map must match a keyboard model by its vendor and product, never
one unit by a port, a unique id or a serial. A profile holds plain files, at most 200 of them and
8 MiB in all, with no link or submodule, and every file it names lies inside
its folder. No name in it holds a control character, and none starts with
`.git`, since Git would read such a file in your supplement. Its
`profile.yaml` writes every value out: an alias (`*name`) is refused.

If a profile should be able to share something it may not, that is a change to
Shaffy OS ([fix it upstream](#fix-it-upstream)).

## Update a profile

`./bin/shaffy outdated` lists each vendored profile whose branch or tag has
moved since you vendored it, and the ones pinned to a commit. Take the new
commit with:

```sh
./bin/shaffy profile update alice/writing   # or every vendored profile, without an ID
```

The command prints the author's commit subjects that touched the profile
between the two commits, a compare link on GitHub, and every entry it adds,
changes or removes, the code-running ones first. It refuses a copy you edited
by hand ([fix it upstream](#fix-it-upstream) first), and it keeps the copy as
it is when the new commit does not load here. `--ref` changes what the profile
follows from then on, and a 40-hex commit pins it. Read the change with
`git diff -- profiles`, then check, commit and apply as when you added it.

## Removing a profile

1. Delete the profile's line from `profiles:` in `config.yaml`.
2. Delete the vendored copy and its lock entry:

   ```sh
   ./bin/shaffy profile remove alice/writing
   ```

3. Commit both, then apply.

The command refuses while `config.yaml` still lists the profile. It also
refuses a copy you edited by hand, and a folder under `profiles/` that has no
lock entry, since deleting either would lose your own work: draft the edits
for the author first ([fix it upstream](#fix-it-upstream)), or commit them in
your supplement, then run it again with `--force`. It prints what stays on the
machine, and the author's link if you removed the profile because something
was wrong with it. The same rules hold when an update drops an entry from a
profile:

| What the profile set | At the next apply |
| --- | --- |
| A home fragment | Its file leaves the home. |
| A keyboard map | Its part leaves the hardware database at the next system apply. |
| An asset | Stays in the home, unmanaged, as a supplement's asset does. |
| `packages.extra` | Stays installed; `packages.remove` in `config.yaml` uninstalls it. |
| `lazyvim.extras` | Leaves `lazyvim.json`; `:Lazy clean` deletes its plugin files. |
| `ai.claude_plugins` | Stays installed and enabled. |
| `ai.mcp_servers` | Stays in the AI clients, or the public default returns; `enabled: false` in `config.yaml` removes it. |
| `requires` | Nothing. The integrations' states are the supplement's. |

## Fix it upstream

A vendored copy stays exactly as its author published it. Never edit a file
under `profiles/` to change what it does. When a profile is wrong or lacks
something, fix it for everyone who uses it:

1. Put what your machine needs now in `config.yaml`, which wins over every
   profile, with a comment that names the issue it waits for.
2. Make the fix in the vendored copy, then draft it for the author:

   ```sh
   ./bin/shaffy profile propose alice/writing > writing.patch
   ```

   The patch applies with `git apply` in the author's repository at the
   vendored commit. The command writes it to a file or a pipe, never to the
   terminal, which would act on any escape sequence in it. It also prints
   where to send it: the author's fork and a prefilled issue on GitHub. It
   names each line of the patch that looks personal, by its rule, never the
   value. A binary file is scanned as bytes only, which misses a value its
   format encodes, such as a photo's position, so check it yourself. The patch
   holds only the files the lock lists: a file you added is named and left
   out, for you to add to the author's repository if it belongs there.
3. Restore the copy with `git restore profiles/alice/writing`, so that the
   loads accept it again.
4. Read the patch, then open the issue or the pull request yourself. Nothing
   is sent for you.

A need that no profile can express goes to Shaffy OS instead: a setting a
profile may not set, or a mechanism profiles lack.

```sh
./bin/shaffy profile propose alice/writing --to shaffy --key desktop.browser
./bin/shaffy profile propose --to shaffy     # a mechanism, without a profile
```

The draft follows the Configuration form of Shaffy OS's issues: a title, the
configuration with the refused key's reason, where it should go and why. It
names the profile, its commit and your Shaffy OS release, and nothing else
from your supplement. The command prints the link that opens the form. A
problem in `profile check` itself, such as a rule that refuses what it should
not, goes the same way.

The scans in `propose` and `profile check` use the self-test's rules for
personal values, this machine's account, host and Git identity, and your
supplement's `denylist.txt` and identifying settings
([credentials and the denylist](SUPPLEMENT.md#credentials-and-the-denylist)).

## Share yours

`./bin/shaffy status` names what your `config.yaml` sets that a profile could
share: fragments, assets, extra packages, editor extras, plugins and MCP
servers. When such a setting names no person or machine and others would want
it, move it into a profile repository of your own:

1. Start the repository from `profile.example/` ([write a profile](#write-a-profile)).
2. Move the fragment or file into a profile's folder, name it in
   `profile.yaml`, and replace every personal value with a setting that stays
   in your `config.yaml`.
3. Run `./bin/shaffy profile check` in the repository, then publish it.
4. In your supplement, delete those settings from `config.yaml`, vendor your
   profile with `./bin/shaffy profile add`, and list it. `./bin/shaffy data`
   shows the same settings, now set by the profile's layer, and
   `./bin/shaffy diff` shows the fragments that move to their profile places.

When everyone who installs Shaffy OS should have the setting, propose it as a
public default instead ([CONTRIBUTING.md](../CONTRIBUTING.md#propose-a-feature-or-a-configuration)).

## Write a profile

`profile.example/` in this checkout is a working profile repository with one
profile:

```text
my-profiles/
├── README.md                    # what each profile does, how to use it, and how to contribute
├── LICENSE                      # MIT with the year and the holder to fill in; choose your own
├── literals.allow               # synthetic values the personal-value scan may pass
├── .github/workflows/shaffy.yml # CI: Shaffy OS's profiles.yml workflow
└── example/                     # one profile: its name is the folder's
    ├── profile.yaml
    ├── README.md
    └── hello.txt
```

```sh
cp -r ~/Projects/shaffy-os/profile.example ~/Projects/my-profiles
git -C ~/Projects/my-profiles init
```

Each top-level folder with a `profile.yaml` is one profile. Its name is
lower-case letters, digits and hyphens. People vendor only the `profile.yaml`,
the files it names and the folder's `README.md`, so a profile's tests and notes
can live beside them.

The repository needs a licence: a `LICENSE` file or a `LICENSES/` folder at
its root. Everyone who vendors a profile copies its files into a repository of
their own, and the licence says they may. Fonts and citation styles often come
with licences of their own. Keep them, and name them in the README.

Check the repository before you publish it, and after every change:

```sh
./bin/shaffy profile check ~/Projects/my-profiles
```

The check reads the repository and changes nothing in it:

1. Each profile loads as a vendored copy would: the schema, the keys a profile
   may not set with their reasons, references inside the folder, refused
   targets, keyboard maps and `minimum_release`.
2. Every file a profile names is tracked by Git, since vendoring reads a
   commit, and no profile holds a link, a submodule or more than the limits.
3. The licence is there.
4. No file holds a personal value. The scan uses the self-test's rules, this
   machine's identity, and the bound supplement's denylist and identifying
   settings (`--supplement PATH` names another supplement). It prints the file, the line and the
   rule, never the value. `literals.allow` passes a synthetic value one of the
   general rules matches.
5. Every file passes [STYLE.md](../STYLE.md)'s tools, with this checkout's
   configuration.
6. Each profile renders alone, then all of them together in folder order, in a
   temporary supplement with the integrations they require managed. The render
   runs in the sandbox, validates Niri, Noctalia and Fish, and checks that a
   second apply changes nothing. Two profiles that conflict together are a
   note. Someone who lists both settles the conflict in `config.yaml`.

The scaffold's `.github/workflows/shaffy.yml` runs the same check on every
push, with Shaffy OS's reusable workflow `.github/workflows/profiles.yml`, and
scans the history for secrets. `schema/profile.schema.json` validates
`profile.yaml` in an editor through the line at the top of the example.

When the check passes, publish the repository:

1. Push it to a public repository.
2. Add the topic `shaffy-os-profiles` to it.
3. Propose it for [the list below](#profile-repositories) with a pull request
   against `dev`.

Keep the README's "Contributing" section. It asks the people who use your
profiles to send their fixes back to you.

## Profile repositories

Shaffy OS curates no profile. The repositories below belong to their authors,
who answer for them. Read what you vendor. More are under the GitHub topic
[shaffy-os-profiles](https://github.com/topics/shaffy-os-profiles).

| Repository | Profiles |
| --- | --- |
| None yet | Publish yours ([write a profile](#write-a-profile)) and propose it here with a pull request against `dev`. |

To add a repository, add a row with its profiles, in alphabetical order, and
open a pull request against `dev`.
