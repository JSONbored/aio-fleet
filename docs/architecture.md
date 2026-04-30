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
5. The reusable workflow checks the caller files against the manifest-rendered output, so workflow drift is caught centrally instead of through duplicated app-local unit tests.
6. Publish gates, Docker cache behavior, integration test gating, release PRs, upstream monitoring, and catalog sync behavior live in reusable workflows.
7. Shared policy checks live in `aio-fleet` validators: caller drift, pinned actions, declared catalog assets, template metadata, publish-platform sanity, and catalog readiness.

Current and later layers are deliberately separate:

- OpenTofu manages public GitHub-owned state: repository settings, branch protections, topics, descriptions, selected action allowlists, vulnerability alerts, and declared Actions variables/secrets names. v1 uses local state and keeps `unraid-aio-template` documented/manual because private-repo branch protection access is blocked by current API access.
- `sync-boilerplate` manages reusable repo boilerplate: docs patterns, tests,
  issue templates, support-thread templates, and shared compatibility shims.
  App-local `scripts/validate-derived-repo.sh` files should stay thin and call
  `aio-fleet validate-derived`; app-specific XML/env validation remains local.
- `sync-catalog` moves manifest-declared XML/icon assets into `awesome-unraid`, refuses unpublished XML, and supports icon-only staged launches.
- App runtime surfaces stay app-local until there is a proven shared abstraction.

## Why This Shape

The fleet is many similar repos with real app-specific exceptions. A monorepo would make Community Apps packaging, release provenance, and app-specific ownership worse. Pure copy/paste keeps every repo independent but makes every CI or release-policy correction multiply across the fleet.

This control-plane model keeps app repos independent while moving repeat policy
into one tested place.
