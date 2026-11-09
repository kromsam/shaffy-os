# Rebuilds a lost state from GitHub: the first plan reports each of these as an import.
# tofu test cannot run import blocks, so the test turns them off; for_each makes the block
# empty when var.import_existing is false.
variable "import_existing" {
  description = "Import the existing repository settings into the state."
  type        = bool
  default     = true
}

import {
  for_each = var.import_existing ? toset(["x"]) : toset([])
  to       = github_repository.shaffy_os
  id       = "shaffy-os"
}

import {
  for_each = var.import_existing ? toset(["x"]) : toset([])
  to       = github_repository_vulnerability_alerts.shaffy_os
  id       = "shaffy-os"
}

import {
  for_each = var.import_existing ? toset(["x"]) : toset([])
  to       = github_repository_dependabot_security_updates.shaffy_os
  id       = "shaffy-os"
}

import {
  for_each = var.import_existing ? toset(["x"]) : toset([])
  to       = github_actions_repository_permissions.shaffy_os
  id       = "shaffy-os"
}

import {
  for_each = var.import_existing ? toset(["x"]) : toset([])
  to       = github_workflow_repository_permissions.shaffy_os
  id       = "shaffy-os"
}

import {
  for_each = var.import_existing ? toset(["x"]) : toset([])
  to       = github_repository_ruleset.protected_branches
  id       = "shaffy-os:26881272"
}

import {
  for_each = var.import_existing ? toset(["x"]) : toset([])
  to       = github_repository_ruleset.main_checks
  id       = "shaffy-os:26881286"
}

import {
  for_each = var.import_existing ? toset(["x"]) : toset([])
  to       = github_repository_ruleset.release_tags
  id       = "shaffy-os:26881257"
}
