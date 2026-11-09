source /usr/share/cachyos-fish-config/cachyos-config.fish

# Open interactive terminals directly at the prompt.
function fish_greeting
end

# The profiles' fragments, in the supplement's list order (docs/PROFILES.md).
source $__fish_config_dir/profiles.fish

# The supplement's optional fragment runs last, so it can replace anything above,
# including functions from conf.d, which fish reads before this file.
source $__fish_config_dir/supplement.fish
