# Omarchy's cd: an existing path changes directory as usual; anything else is a
# zoxide query over directories visited before, so a name or part of one is enough.
if status is-interactive; and command -q zoxide
    zoxide init fish | source
    functions --copy cd __cd_before_zoxide
    function cd --description 'Change directory, asking zoxide when the path does not exist'
        if test (count $argv) -eq 0; or string match -q -- '-*' "$argv[1]"; or test -d "$argv[1]"
            __cd_before_zoxide $argv
            return
        end
        set -l target (command zoxide query --exclude "$PWD" -- $argv 2>/dev/null)
        or begin
            echo "cd: no directory or remembered match for '$argv'" >&2
            return 1
        end
        __cd_before_zoxide $target
        and echo \U000F17A9" $PWD"
    end
end
