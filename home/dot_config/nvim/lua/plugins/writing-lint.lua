local function read_json(path)
  if vim.fn.filereadable(path) ~= 1 then
    return nil
  end

  local ok, decoded = pcall(vim.json.decode, table.concat(vim.fn.readfile(path), "\n"))
  if ok and type(decoded) == "table" then
    return decoded
  end
end

local harper_settings = {
  ["harper-ls"] = {
    workspaceDictPath = ".harper-dictionary.txt",
    diagnosticSeverity = "hint",
    dialect = "British",
    isolateEnglish = false,
    maxFileLength = 500000,
    markdown = {
      IgnoreLinkTitle = true,
    },
    linters = {
      SpellCheck = true,
      SpelledNumbers = false,
      AnA = true,
      SentenceCapitalization = true,
      UnclosedQuotes = true,
      WrongApostrophe = false,
      LongSentences = true,
      RepeatedWords = true,
      Spaces = true,
      CorrectNumberSuffix = true,
    },
    codeActions = {
      ForceStable = true,
    },
    excludePatterns = {
      "**/.git/**",
      "**/node_modules/**",
      "**/_site/**",
      "**/dist/**",
      "**/build/**",
      "**/output/**",
      "**/*.html",
      "**/*.pdf",
      "**/*.docx",
    },
  },
}

return {
  {
    "neovim/nvim-lspconfig",
    opts = {
      servers = {
        harper_ls = {
          filetypes = { "markdown", "gitcommit" },
          settings = harper_settings,
          on_new_config = function(config, root_dir)
            if not root_dir then
              return
            end

            local project_settings = read_json(root_dir .. "/.vale/harper-ls-settings.json")
            if project_settings then
              config.settings = vim.tbl_deep_extend("force", config.settings or {}, project_settings)
            end
          end,
        },
      },
    },
  },
}
