"""Which chezmoi commands may change the home outside ./bin/shaffy.

Three layers apply one rule; they are accident guards, not a security boundary
against someone who controls the account and source:

- the configuration hooks in the tracked chezmoi.toml call `bin/shaffy hook`,
  which runs this module before any command reads the source;
- home/.chezmoiignore.tmpl refuses when a command reads the source state, even
  without the tracked chezmoi.toml;
- home/.chezmoi.toml.tmpl refuses every `chezmoi init`, because init renders it
  before it applies or purges.

`chezmoi purge`, and the git pull that `chezmoi update` runs first, never read
the source, so only the hooks stop them: run chezmoi with this source only
through the tracked chezmoi.toml. An unguarded purge deletes the whole checkout,
.git included, but does not change the home.

Flags are read the way chezmoi's flag parser reads them: a boolean flag may
carry a value (`--apply=1`, `-a=true`, `-va=t`), and only an explicit false
value leaves it off. Every argument is read, even after `--`, because chezmoi takes
`--` as the value of a flag that expects one (`edit -o -- --apply`). The template layer also refuses
`edit` when the user's own configuration sets edit.apply or edit.watch; the
hook cannot see that configuration, and `check` refuses a tracked chezmoi.toml
that sets them.

Only the template layer honours the render sandbox, and only while the
sandbox's token file named by SHAFFY_SANDBOX_TOKEN exists, so an exported
SHAFFY_OP_MODE=sandbox alone lets nothing through.
"""

import re

FALSE = '(0|f|F|false|FALSE|False)'
ALWAYS = ('apply', 'update', 'destroy', 'purge', 'init')
# The long and short flags that make a command change the home.
FLAGS = {'edit': ('apply|watch', 'a')}


def _sets(arguments, long, short):
    long_on = re.compile(rf'^--({long})(=.*)?$')
    long_off = re.compile(rf'^--({long})={FALSE}$')
    short_on = re.compile(rf'^-[a-zA-Z]*[{short}][a-zA-Z]*(=.*)?$')
    short_off = re.compile(rf'[{short}]={FALSE}$')
    for argument in arguments:
        if long_on.match(argument) and not long_off.match(argument):
            return True
        if not argument.startswith('--') and short_on.match(short_off.sub('', argument)):
            return True
    return False


def refusal(command, arguments, mode=None, edit_apply=False, edit_watch=False):
    """Return a refusal message, or None when the command may run."""
    if mode == 'apply':
        return None
    mutating = command in ALWAYS or (command in FLAGS and _sets(arguments, *FLAGS[command]))
    if command == 'edit' and (edit_apply or edit_watch):
        mutating = True
    if not mutating:
        return None
    return (
        f'Shaffy OS: run ./bin/shaffy apply instead of chezmoi {command}; direct changes '
        'bypass its snapshots and records.'
    )
