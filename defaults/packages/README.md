# Package groups

Each `<group>.yml` is installed by the role of the same name. `packages` lists
repository packages for `community.general.pacman`, in the configured
CachyOS/Arch repository priority and without refreshing the databases (a full
`pacman -Syu` stays a separate step). The optional `aur` list names packages
built by the `aur_package` role, with entries such as
`{name: zotero-bin, replaces: zotero}`. Both lists participate in the same
selection rules. `required` lists packages the role's own later tasks use,
which a supplement's `packages.omit` may not drop. A package belongs to one
group only, whether it comes from a repository or the AUR.

The loader emits repository selections under `shaffy.packages.groups.GROUP`
and AUR selections under `shaffy.packages.aur.GROUP`. Roles consume those
filtered lists; they must not keep separate package names in their tasks.
An AUR package and the package it replaces cannot both be selected.

`retired.yml` is the public retirement list: those packages are
removed only because they are listed, with `pacman -R`, keeping their
dependencies and private profiles. A supplement's `packages.extra` installs
additional repository packages, `packages.omit` leaves group packages out without uninstalling them, and
`packages.remove` removes more; the `packages` role owns extras and removals.
To uninstall a public package and keep it from being installed again, list it
in both `omit` and `remove` and apply all system roles, or at least the
`packages` role and the package's group role.
