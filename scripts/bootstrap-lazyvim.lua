-- Run from the repo with: mise exec -- nvim --headless -l scripts/bootstrap-lazyvim.lua
-- Load the managed configuration, complete asynchronous tool installs, then exit.
vim.go.loadplugins = true
dofile(vim.fn.stdpath("config") .. "/init.lua")
vim.api.nvim_exec_autocmds("User", { pattern = "VeryLazy" })
require("lazy").load({ plugins = { "mason.nvim", "mason-lspconfig.nvim" } })
local registry = require("mason-registry")

-- The Mason packages LazyVim installs for the enabled extras, public and the supplement's:
-- its tools, and the language servers it sets up through Mason, decided as in
-- lazyvim/plugins/lsp/init.lua.
local function wanted_packages()
  local wanted = {}
  for _, tool in ipairs(LazyVim.opts("mason.nvim").ensure_installed or {}) do
    wanted[tool] = true
  end
  local packages = require("mason-lspconfig.mappings").get_mason_map().lspconfig_to_package
  for server, options in pairs(LazyVim.opts("nvim-lspconfig").servers or {}) do
    options = options == true and {} or not options and { enabled = false } or options
    if server ~= "*" and options.enabled ~= false and options.mason ~= false and packages[server] then
      wanted[packages[server]] = true
    end
  end
  for _, server in ipairs(LazyVim.opts("mason-lspconfig.nvim").ensure_installed or {}) do
    wanted[packages[server] or server] = true
  end
  local names = vim.tbl_keys(wanted)
  table.sort(names)
  return names
end

local wanted = wanted_packages()
-- An empty list means LazyVim's options moved, not that nothing needs installing.
if #wanted == 0 then
  print("No Mason packages found in LazyVim's options")
  vim.cmd("cq")
end
registry.refresh(function()
  local pending = 0
  local failures = {}
  for _, name in ipairs(wanted) do
    if not registry.has_package(name) then
      table.insert(failures, name .. ": absent from registry")
    else
      local pkg = registry.get_package(name)
      if not pkg:is_installed() then
        pending = pending + 1
        local handle = pkg:is_installing() and pkg:get_install_handle():get() or pkg:install()
        handle:once("closed", function()
          if not pkg:is_installed() then
            table.insert(failures, name)
          end
          pending = pending - 1
        end)
      end
    end
  end
  local timer = vim.uv.new_timer()
  timer:start(
    1000,
    1000,
    vim.schedule_wrap(function()
      if pending == 0 then
        timer:stop()
        timer:close()
        local result = vim.json.encode({ tools = #wanted, failures = failures })
        vim.fn.writefile({ result }, vim.fn.stdpath("state") .. "/desktop-tools.json")
        print(result)
        vim.cmd(#failures == 0 and "qa!" or "cq")
      end
    end)
  )
end)
vim.defer_fn(function()
  print("Tool installation timeout")
  vim.cmd("cq")
end, 600000)
vim.wait(610000, function()
  return false
end, 100)
