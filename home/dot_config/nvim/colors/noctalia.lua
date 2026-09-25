local path = vim.fn.stdpath("config") .. "/lua/noctalia_palette.lua"
if not vim.uv.fs_stat(path) then
  -- Permit a first launch before Noctalia has rendered the initial palette.
  vim.cmd.colorscheme("matteblack")
  return
end
require("base16-colorscheme").setup(dofile(path))
vim.g.colors_name = "noctalia"
