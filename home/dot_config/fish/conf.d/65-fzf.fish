# fzf's own keys: Ctrl+R searches the command history, Ctrl+T inserts file
# paths and Alt+C changes to a directory below the current one. CachyOS's Fish
# configuration installs fzf; without it the shell keeps its own keys.
if status is-interactive; and command -q fzf
    fzf --fish | source
end
