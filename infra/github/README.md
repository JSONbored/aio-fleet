# GitHub Infrastructure

This directory is the OpenTofu/Terraform home for GitHub-owned fleet state.

It is intentionally separate from source-file sync. Use it for repository
settings, topics, branch protections, required checks, Actions allowlists,
vulnerability alerts, and declared secret/variable names. Do not store secret
values here.

## Local State

v1 uses local OpenTofu state. State files and local `.tfvars` files are ignored
by git; `.terraform.lock.hcl` is tracked so everyone uses the same provider
selection.

```bash
cd infra/github
tofu init
cp repos.tfvars.example repos.tfvars
tofu plan -var-file=repos.tfvars
```

## Adoption

`imports.tf` declares imports for the public active fleet:

- `awesome-unraid`
- `sure-aio`
- `simplelogin-aio`
- `khoj-aio`
- `mem0-aio`
- `infisical-aio`
- `dify-aio`
- `signoz-aio`

Run `tofu plan` once after `tofu init`; OpenTofu will adopt those resources into
local state. The private `unraid-aio-template` repo is intentionally documented
outside this v1 module until branch-protection API access is available for it.

## Managed State

The module currently manages:

- public repo metadata, topics, homepage, and basic feature toggles
- `main` branch required status checks
- signed commits, conversation resolution, review requirements, and strict checks
- selected GitHub Actions allowlists
- SHA pinning for selected actions
- vulnerability alerts through `github_repository_vulnerability_alerts`
- declared secret and variable names as outputs only

Secret values are not managed. `AIO_FLEET_BOT_TOKEN` is declared for
`awesome-unraid`, and the CLI can verify that the secret exists.

## Action Allowlist Model

The selected-actions allowlist uses:

```text
JSONbored/aio-fleet/.github/workflows/aio-*.yml@*
```

GitHub SHA pinning remains enabled, so callers must still use a full commit SHA.
This avoids per-repo allowlist churn every time `aio-fleet` publishes a new
workflow commit while preserving the security property that reusable workflow
calls are pinned.
