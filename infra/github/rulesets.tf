# No bypass actors and no pull request rule: a release fast-forwards main to a
# tested dev commit, and the maintainer pushes to dev directly (MAINTAINING.md).
resource "github_repository_ruleset" "protected_branches" {
  repository  = github_repository.shaffy_os.name
  name        = "main and dev"
  target      = "branch"
  enforcement = "active"

  conditions {
    ref_name {
      include = ["refs/heads/main", "refs/heads/dev"]
      exclude = []
    }
  }

  rules {
    deletion         = true
    non_fast_forward = true
  }
}

# 15368 is GitHub Actions, the app that reports the CI jobs.
resource "github_repository_ruleset" "main_checks" {
  repository  = github_repository.shaffy_os.name
  name        = "main checks"
  target      = "branch"
  enforcement = "active"

  conditions {
    ref_name {
      include = ["refs/heads/main"]
      exclude = []
    }
  }

  rules {
    required_status_checks {
      strict_required_status_checks_policy = false

      required_check {
        context        = "selftest"
        integration_id = 15368
      }

      required_check {
        context        = "system"
        integration_id = 15368
      }
    }
  }
}

resource "github_repository_ruleset" "release_tags" {
  repository  = github_repository.shaffy_os.name
  name        = "release tags"
  target      = "tag"
  enforcement = "active"

  conditions {
    ref_name {
      include = ["refs/tags/v*"]
      exclude = []
    }
  }

  rules {
    deletion = true
    update   = true
  }
}
