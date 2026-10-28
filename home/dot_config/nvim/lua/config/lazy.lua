-- Modified for Shaffy OS: retain the managed plugin versions during first-install discovery.
local lockfile = vim.fn.stdpath("config") .. "/lazy-lock.json"
local locked = vim.json.decode(table.concat(vim.fn.readfile(lockfile), "\n"))
local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not (vim.uv or vim.loop).fs_stat(lazypath) then
  local lazyrepo = "https://github.com/folke/lazy.nvim.git"
  local out = vim.fn.system({ "git", "clone", "--filter=blob:none", "--branch=stable", lazyrepo, lazypath })
  if vim.v.shell_error == 0 then
    out = vim.fn.system({ "git", "-C", lazypath, "checkout", "--detach", locked["lazy.nvim"].commit })
  end
  if vim.v.shell_error ~= 0 then
    vim.api.nvim_echo({
      { "Failed to clone lazy.nvim:\n", "ErrorMsg" },
      { out, "WarningMsg" },
      { "\nPress any key to exit..." },
    }, true, {})
    vim.fn.getchar()
    os.exit(1)
  end
end
vim.opt.rtp:prepend(lazypath)

local spec = {
  { "LazyVim/LazyVim", import = "lazyvim.plugins" },
  { import = "plugins" },
}
-- LazyVim discovers some plugins only after its own first installation. lazy.nvim
-- rewrites the lockfile between those rounds, dropping versions of plugins it has
-- not discovered yet. Read the managed versions once, before setup starts, and
-- retain them as optional overrides for every discovery round. scripts/refresh-pins.py
-- sets SHAFFY_LAZY_REFRESH in its sandbox, so that every plugin can move on.
if not vim.env.SHAFFY_LAZY_REFRESH then
  for name, version in pairs(locked) do
    spec[#spec + 1] = { name, optional = true, commit = version.commit }
  end
end

require("lazy").setup({
  spec = spec,
  defaults = {
    -- By default, only LazyVim plugins will be lazy-loaded. Your custom plugins will load during startup.
    -- If you know what you're doing, you can set this to `true` to have all your custom plugins lazy-loaded by default.
    lazy = false,
    -- It's recommended to leave version=false for now, since a lot the plugin that support versioning,
    -- have outdated releases, which may break your Neovim install.
    version = false, -- the explicit commits above take precedence
    -- version = "*", -- try installing the latest stable version for plugins that support semver
  },
  install = { colorscheme = { "tokyonight", "habamax" } },
  checker = {
    enabled = true, -- check for plugin updates periodically
    notify = false, -- notify on update
  }, -- automatically check for plugin updates
  performance = {
    rtp = {
      -- disable some rtp plugins
      disabled_plugins = {
        "gzip",
        -- "matchit",
        -- "matchparen",
        -- "netrwPlugin",
        "tarPlugin",
        "tohtml",
        "tutor",
        "zipPlugin",
      },
    },
  },
})
