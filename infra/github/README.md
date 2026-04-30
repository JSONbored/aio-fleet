# GitHub Infrastructure

This directory is the OpenTofu/Terraform home for GitHub-owned fleet state.

It is intentionally separate from source-file sync. Use it for repository
settings, topics, branch rulesets, required checks, Actions settings, labels,
environments, and secret/variable names. Do not store secret values here.

## Current Slice

The first scaffold defines repository metadata and required-check policy inputs.
Apply it only after confirming the required check names for every repo after the
current reusable workflow rollout has settled.

```bash
cd infra/github
tofu init
tofu plan -var-file=repos.tfvars
```

Use `repos.tfvars.example` as the starting point for real local state. Keep real
state out of git unless a remote backend is deliberately configured.
