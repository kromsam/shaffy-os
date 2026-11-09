import {
  to = github_repository.shaffy_os
  id = "shaffy-os"
}

resource "github_repository" "shaffy_os" {
  name                   = "shaffy-os"
  description            = "An opinionated desktop setup for CachyOS with Niri and Noctalia, applied from public defaults and a private supplement"
  visibility             = "public"
  has_discussions        = false
  has_issues             = true
  has_projects           = true
  has_wiki               = false
  allow_auto_merge       = false
  allow_forking          = true
  allow_merge_commit     = true
  allow_rebase_merge     = true
  allow_squash_merge     = true
  allow_update_branch    = false
  delete_branch_on_merge = false

  merge_commit_title          = "MERGE_MESSAGE"
  merge_commit_message        = "PR_TITLE"
  squash_merge_commit_title   = "COMMIT_OR_PR_TITLE"
  squash_merge_commit_message = "COMMIT_MESSAGES"
  web_commit_signoff_required = false

  security_and_analysis {
    secret_scanning {
      status = "enabled"
    }
    secret_scanning_push_protection {
      status = "enabled"
    }
  }

  # prevent_destroy leaves with this block; archiving still saves the history.
  archive_on_destroy = true

  lifecycle {
    prevent_destroy = true
  }
}

import {
  to = github_repository_vulnerability_alerts.shaffy_os
  id = "shaffy-os"
}

resource "github_repository_vulnerability_alerts" "shaffy_os" {
  repository = github_repository.shaffy_os.name
  enabled    = true
}

import {
  to = github_repository_dependabot_security_updates.shaffy_os
  id = "shaffy-os"
}

resource "github_repository_dependabot_security_updates" "shaffy_os" {
  repository = github_repository.shaffy_os.name
  enabled    = true
}

import {
  to = github_actions_repository_permissions.shaffy_os
  id = "shaffy-os"
}

resource "github_actions_repository_permissions" "shaffy_os" {
  repository           = github_repository.shaffy_os.name
  enabled              = true
  allowed_actions      = "all"
  sha_pinning_required = false
}

import {
  to = github_workflow_repository_permissions.shaffy_os
  id = "shaffy-os"
}

resource "github_workflow_repository_permissions" "shaffy_os" {
  repository                       = github_repository.shaffy_os.name
  default_workflow_permissions     = "read"
  can_approve_pull_request_reviews = false
}
