local path = vim.fn.stdpath("config") .. "/lua/noctalia_palette.lua"
-- Until Noctalia renders the first palette, base16's Rosé Pine Dawn, the default palette's light
-- variant, stands in; the palette reload on focus replaces it. A nested :colorscheme would be ignored.
require("base16-colorscheme").setup(vim.uv.fs_stat(path) and dofile(path) or "rose-pine-dawn")
vim.g.colors_name = "noctalia"
