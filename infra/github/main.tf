provider "github" {
  owner = var.github_owner
}

resource "github_repository" "aio" {
  for_each = var.repositories

  name             = each.key
  description      = each.value.description
  visibility       = each.value.visibility
  homepage_url     = each.value.homepage_url
  has_issues       = each.value.has_issues
  allow_auto_merge = each.value.allow_auto_merge
  topics           = each.value.topics

  vulnerability_alerts = true
}

locals {
  required_checks = {
    for name, repo in var.repositories : name => repo.required_checks
  }
}
