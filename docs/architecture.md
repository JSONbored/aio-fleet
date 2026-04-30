# AIO Fleet Architecture

`aio-fleet` is the control plane for the JSONbored Unraid AIO portfolio.

It does not replace the existing source-of-truth repos:

- `unraid-aio-template` remains the bootstrap template for new app repos.
- App repos remain product/runtime repos with their Dockerfile, rootfs, XML, tests, and docs.
- `awesome-unraid` remains the Community Apps-facing catalog and icon repository.
- `aio-fleet` owns fleet policy, shared workflow behavior, validation, and drift reporting.

## Control-Plane Layers

The first layer is reusable GitHub Actions:

1. App repos keep a small `.github/workflows/build.yml` caller.
2. App repos also keep small callers for upstream checks and release workflows.
3. Each caller pins `JSONbored/aio-fleet/.github/workflows/*.yml` to a full commit SHA.
4. Repo-specific behavior is passed as explicit inputs from `fleet.yml`.
5. Publish gates, Docker cache behavior, integration test gating, release PRs, upstream monitoring, and catalog sync behavior live in reusable workflows.

Later layers are deliberately separate:

- Terraform/OpenTofu should manage GitHub-owned state: repository settings, rulesets, branch protections, topics, descriptions, Actions variables/secrets names, and environments.
- Copier should manage reusable repo boilerplate: docs patterns, tests, issue templates, support-thread templates, and shared helper scripts.
- App runtime surfaces stay app-local until there is a proven shared abstraction.

## Why This Shape

The fleet is many similar repos with real app-specific exceptions. A monorepo would make Community Apps packaging, release provenance, and app-specific ownership worse. Pure copy/paste keeps every repo independent but makes every CI or release-policy correction multiply across the fleet.

This control-plane model keeps app repos independent while moving repeat policy
into one tested place.
