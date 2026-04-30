variable "github_owner" {
  description = "GitHub owner or organization that owns the AIO repositories."
  type        = string
  default     = "JSONbored"
}

variable "repositories" {
  description = "GitHub-owned repository state managed by the fleet."
  type = map(object({
    description     = string
    visibility      = optional(string, "public")
    topics          = optional(list(string), [])
    homepage_url    = optional(string, "")
    has_issues      = optional(bool, true)
    allow_auto_merge = optional(bool, false)
    required_checks = optional(list(string), [])
  }))
}
