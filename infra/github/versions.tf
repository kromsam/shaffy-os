terraform {
  required_version = "= 1.12.6"

  required_providers {
    github = {
      source  = "integrations/github"
      version = "= 6.13.0"
    }
  }

  # scripts/github-settings.sh init sets the path, outside every checkout and worktree.
  # The state holds only settings GitHub shows publicly, so it is not encrypted.
  backend "local" {}
}
