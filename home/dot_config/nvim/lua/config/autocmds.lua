-- Autocmds are automatically loaded on the VeryLazy event
-- Default autocmds that are always set: https://github.com/LazyVim/LazyVim/blob/main/lua/lazyvim/config/autocmds.lua
--
-- Add any additional autocmds here
-- with `vim.api.nvim_create_autocmd`
--
-- Or remove existing autocmds by their group name (which is prefixed with `lazyvim_` for the defaults)
-- e.g. vim.api.nvim_del_augroup_by_name("lazyvim_wrap_spell")
--
-- Modified for Shaffy OS, 2026: reload the Noctalia palette on focus.

-- Pick up regenerated colors when returning to the editor, without signalling
-- unrelated Neovim processes or interrupting a manually selected colorscheme.
local palette_path = vim.fn.stdpath("config") .. "/lua/noctalia_palette.lua"
local last_palette = vim.uv.fs_stat(palette_path)
vim.api.nvim_create_autocmd({ "FocusGained", "VimResume" }, {
  group = vim.api.nvim_create_augroup("noctalia_palette", { clear = true }),
  callback = function()
    if vim.g.colors_name ~= "noctalia" then
      return
    end
    local current = vim.uv.fs_stat(palette_path)
    if current and (not last_palette
      or current.mtime.sec ~= last_palette.mtime.sec
      or current.mtime.nsec ~= last_palette.mtime.nsec) then
      vim.cmd.colorscheme("noctalia")
      last_palette = current
    end
  end,
})
