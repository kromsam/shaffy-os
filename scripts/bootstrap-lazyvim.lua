-- Run from the repo with: mise exec -- nvim --headless -l scripts/bootstrap-lazyvim.lua
-- Load the managed configuration, complete asynchronous tool installs, then exit.
vim.go.loadplugins = true
dofile(vim.fn.stdpath('config') .. '/init.lua')
vim.api.nvim_exec_autocmds('User', { pattern = 'VeryLazy' })
require('lazy').load({ plugins = { 'mason.nvim' } })
local registry = require('mason-registry')
local wanted = {
  'stylua', 'shfmt', 'hadolint', 'harper-ls', 'markdown-toc', 'markdownlint-cli2',
  'erb-formatter', 'erb-lint', 'ruby-lsp', 'rubocop', 'lua-language-server',
  'json-lsp', 'yaml-language-server', 'taplo', 'tailwindcss-language-server',
  'dockerfile-language-server', 'docker-compose-language-service', 'marksman',
}
registry.refresh(function()
  local pending = 0
  local failures = {}
  for _, name in ipairs(wanted) do
    if not registry.has_package(name) then
      table.insert(failures, name .. ': absent from registry')
    else
      local pkg = registry.get_package(name)
      if not pkg:is_installed() then
        pending = pending + 1
        local handle = pkg:is_installing() and pkg:get_install_handle():get() or pkg:install()
        handle:once('closed', function()
          if not pkg:is_installed() then table.insert(failures, name) end
          pending = pending - 1
        end)
      end
    end
  end
  local timer = vim.uv.new_timer()
  timer:start(1000, 1000, vim.schedule_wrap(function()
    if pending == 0 then
      timer:stop(); timer:close()
      local result = vim.json.encode({ tools = #wanted, failures = failures })
      vim.fn.writefile({ result }, vim.fn.stdpath('state') .. '/desktop-tools.json')
      print(result)
      vim.cmd(#failures == 0 and 'qa!' or 'cq')
    end
  end))
end)
vim.defer_fn(function() print('Tool installation timeout'); vim.cmd('cq') end, 600000)
vim.wait(610000, function() return false end, 100)
