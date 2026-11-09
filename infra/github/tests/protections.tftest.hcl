mock_provider "github" {}

variables {
  import_existing = false
}

run "branches_cannot_be_deleted_or_force_pushed" {
  command = plan

  assert {
    condition = (
      github_repository_ruleset.protected_branches.enforcement == "active" &&
      toset(one(one(github_repository_ruleset.protected_branches.conditions).ref_name).include) == toset(["refs/heads/dev", "refs/heads/main"]) &&
      one(github_repository_ruleset.protected_branches.rules).deletion &&
      one(github_repository_ruleset.protected_branches.rules).non_fast_forward
    )
    error_message = "main and dev must stay active and refuse deletion and force pushes."
  }
}

run "main_requires_the_selftest_and_system_jobs" {
  command = plan

  assert {
    condition = (
      github_repository_ruleset.main_checks.enforcement == "active" &&
      toset(one(one(github_repository_ruleset.main_checks.conditions).ref_name).include) == toset(["refs/heads/main"]) &&
      toset([
        for check in one(one(github_repository_ruleset.main_checks.rules).required_status_checks).required_check :
        "${check.integration_id}:${check.context}"
      ]) == toset(["15368:selftest", "15368:system"])
    )
    error_message = "main must stay active and require the selftest and system checks from GitHub Actions (integration 15368)."
  }
}

run "release_tags_cannot_be_deleted_or_moved" {
  command = plan

  assert {
    condition = (
      github_repository_ruleset.release_tags.enforcement == "active" &&
      toset(one(one(github_repository_ruleset.release_tags.conditions).ref_name).include) == toset(["refs/tags/v*"]) &&
      one(github_repository_ruleset.release_tags.rules).deletion &&
      one(github_repository_ruleset.release_tags.rules).update
    )
    error_message = "The v* tags must stay active and refuse deletion and update."
  }
}

run "no_ruleset_has_a_bypass_actor" {
  command = plan

  assert {
    condition = (
      length(github_repository_ruleset.protected_branches.bypass_actors) == 0 &&
      length(github_repository_ruleset.main_checks.bypass_actors) == 0 &&
      length(github_repository_ruleset.release_tags.bypass_actors) == 0
    )
    error_message = "No ruleset may have a bypass actor."
  }
}
