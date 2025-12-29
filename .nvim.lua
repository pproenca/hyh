-- Project-local neovim configuration for hyh
-- Requires `vim.o.exrc = true` in your neovim config

require("neo-tree").setup({
  filesystem = {
    filtered_items = {
      visible = true, -- Show hidden files (dimmed out)
      hide_dotfiles = false,
      hide_gitignored = true,
    },
  },
})
