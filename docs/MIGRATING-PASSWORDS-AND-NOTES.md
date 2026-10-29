# Migrating to Proton Pass and SilverBullet

<!-- Remove after 2027-01-20, with scripts/convert-logseq-graph.py and the links to this
page in DESKTOP-APPLICATIONS.md. -->

Shaffy OS replaced Bitwarden with Proton Pass and Logseq OG with SilverBullet
Desktop. The system apply no longer installs `bitwarden` or `logseq-og-bin`, and
the home apply removes `bitwarden-login` and no longer names the `bw` CLI, but neither removes an installed
application, its profile or its data. Move the data first, check it, and remove
the old applications only after that.

## Bitwarden to Proton Pass

A vault export holds every password unencrypted, and Proton Pass refuses
Bitwarden's password-protected export. Save it in `$XDG_RUNTIME_DIR`
(`/run/user/UID`), which lives in memory: snapper's home snapshots and the
restic home backup never see it, and it is gone at the next boot.

1. In the Bitwarden web vault, open **Tools → Export vault**, export from
   **My vault** in the `.json` format, and save the file in `$XDG_RUNTIME_DIR`.
   An individual export leaves out organisation items: export each organisation
   that should move as well. Trash is never exported.
2. In the Proton Pass app, open **Settings → Import**, choose **Bitwarden** and
   the file. The items land in one new vault; Bitwarden folders become folders
   inside it. Compare the item counts of both vaults. Proton Pass has had
   folders only since 15 October 2026; an earlier import put everything in one
   vault without them. To redo it, import into a new vault, compare, and then
   delete the old one.
3. Delete the export: `rm "$XDG_RUNTIME_DIR"/bitwarden_export_*.json`.
4. In each browser, install the Proton Pass extension, sign in, and remove the
   Bitwarden extension.

Logins with their two-factor secrets, notes, cards, identities, SSH keys and
text and hidden custom fields move. Passkeys do not: sign in to each site that
has one and register a new passkey with Proton Pass. Attachments move only on
the paid plans that include them, and linked custom fields are dropped. Keep the
Bitwarden account until Proton Pass has been in use for a while, then delete it
or keep it as a frozen copy.

## Logseq to SilverBullet

`scripts/convert-logseq-graph.py` reads a file-based Logseq graph and writes a
new SilverBullet space folder; the graph itself is never changed. Close Logseq,
then convert into a folder that does not exist yet. When the space will sync
with a server, choose a folder that no other sync client, such as Nextcloud or
Syncthing, also syncs: two clients writing the same files produce conflicts.

```sh
python3 scripts/convert-logseq-graph.py ~/path/to/graph ~/Notes
```

In SilverBullet Desktop, choose **New space** and the new folder. The
`Logseq import` page lists what still needs a person: queries it could not
translate, whiteboards, renamed pages, and the lines that only Syncthing
conflict copies held. It is tagged `meta`, so open it with the Meta Picker. To
reach the space from other devices, connect it to a SilverBullet Server under
the space's gear icon, **Sync**; start with an empty server space, because the
first sync merges both sides.

| Logseq | SilverBullet |
| --- | --- |
| `journals/2024_01_15.md`, links like `[[Jan 15th, 2024]]` | `Journal/2024-01-15` with `tags: [journal]` and `date`, which the journal commands navigate by |
| `pages/a___b.md`, `%3A` in file names | the page `a/b`, decoded |
| `title::` | the page name; `displayName` when the name had to change |
| the page's leading `key:: value` lines | frontmatter; `tags::` and `alias::` become `tags` and `aliases` |
| a block's `key:: value` | the attribute `[key: value]`, with dashed keys in camelCase |
| a block's `tag::` or `tags::` | hashtags on the item |
| a property named like a built-in attribute, such as `name::` | `logseqName`, and so on |
| `collapsed::`, `id::`, query and PDF highlight properties, `:LOGBOOK:` | dropped |
| `TODO`, `LATER` / `DONE` / `NOW`, `DOING` / `WAITING` / `CANCELED` | `[ ]` / `[x]` / `[DOING]` / `[WAITING]` / `[CANCELED]` |
| `SCHEDULED:` and `DEADLINE:` | `[scheduled: date]` and `[deadline: date]` |
| `#[[a page]]`, and a `#tag` SilverBullet cannot parse (such as `#@key` or `#2024`) | the link `[[a page]]`; other `#tag`s stay tags |
| a first block of only hashtags | the page's `tags` in frontmatter |
| a first heading that restates the page name | dropped: the name is the title |
| `{{query}}` of page references, task markers, `and`, `or` and `not` | a live query; `query-table`, `query-properties` and `query-sort-by` set its columns and order |
| other queries, macros and `((block references))` | kept as code, listed in `Logseq import` |
| a block with `template::` | a slash template under `Library/Templates/`, inserted with `/name` |
| `{{video}}` of YouTube or Vimeo | an embedded player; other addresses stay bare |
| `{{zotero-imported-file KEY, "name"}}` | a `zotero://open-pdf` link |
| `![alt](../assets/file){:width 400}` | `![[assets/file\|400]]`, a link from the space root |
| empty journals, conflict copies | left out; a conflict copy's new lines go to `Logseq import` |

A converted query treats a tag and a link to a page as the same reference, and
searches items and tasks together, as Logseq searched its blocks. Its helpers
are Space Lua on the meta page `Library/Logseq`. Template pages are meta pages,
so their placeholder entries stay out of query results.

SilverBullet names cannot contain `|`, `@` or `#`, so the converter replaces
`|` and `#` with `-` and drops `@`, and every link follows the new name. Links
to pages without a file stay links, as in Logseq. Keep the graph unchanged
until the space has been checked.

## Removing the old applications

Once both moves check out, the supplement's package choices remove the old
applications:

```yaml
packages:
  remove: [bitwarden, logseq-og-bin]
```

Then run `./bin/shaffy apply --system --tags packages`. Their profiles stay:
`~/.config/Bitwarden`, `~/.config/Logseq` and `~/.logseq` are deleted by hand
when nothing in them is needed any more. `mise prune` removes the Bitwarden CLI,
which the mise configuration no longer names.
