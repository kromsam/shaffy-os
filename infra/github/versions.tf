terraform {
  required_version = "= 1.12.6"

  required_providers {
    github = {
      source  = "integrations/github"
      version = "= 6.13.0"
    }
  }

  # scripts/github-settings.sh init sets the path, outside every checkout and worktree.
  backend "local" {}

  encryption {
    key_provider "pbkdf2" "github_state" {
      passphrase               = var.state_encryption_passphrase
      key_length               = 32
      iterations               = 600000
      salt_length              = 32
      hash_function            = "sha512"
      encrypted_metadata_alias = "shaffy-os-github-state-v1"
    }

    method "aes_gcm" "github_state" {
      keys = key_provider.pbkdf2.github_state
    }

    state {
      method   = method.aes_gcm.github_state
      enforced = true
    }

    plan {
      method   = method.aes_gcm.github_state
      enforced = true
    }
  }
}
