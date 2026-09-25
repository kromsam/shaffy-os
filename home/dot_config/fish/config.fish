source /usr/share/cachyos-fish-config/cachyos-config.fish

# Open interactive terminals directly at the prompt.
function fish_greeting
end

# The supplement's optional fragment runs last, so it can replace anything above,
# including functions from conf.d, which fish reads before this file.
source $__fish_config_dir/supplement.fish
