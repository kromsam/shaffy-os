# Tool versions follow the current project's reviewed mise/version files.
if status is-interactive; and type -q mise
    mise activate fish | source
end
