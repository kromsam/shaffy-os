# Package groups

Each `<group>.yml` is installed by the role of the same name with
`community.general.pacman`, in the configured CachyOS/Arch repository priority
and without refreshing the databases (a full `pacman -Syu` stays a separate
step). `packages` is the group; `required` lists the packages the role's own
later tasks or local package builds use, which a supplement's `packages.omit`
may not drop. A package belongs to one group only.

`retired.yml` is the public retirement list: those packages are
removed only because they are listed, with `pacman -R`, keeping their
dependencies and private profiles. A supplement's `packages.extra` installs
more, `packages.omit` leaves group packages out without uninstalling them, and
`packages.remove` removes more; the `packages` role owns extras and removals.
