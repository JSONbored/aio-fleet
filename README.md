# AIO Fleet

Central control plane for the JSONbored Unraid AIO fleet.

`aio-fleet` keeps the shared CI, release, validation, and drift-management logic
out of individual app repos. App repos stay focused on their runtime wrapper,
Dockerfile, Unraid template, docs, and tests. This repo owns the fleet contract.

## Current slice

- `fleet.yml` is the canonical manifest for app repo metadata and exceptions.
- `.github/workflows/aio-build.yml` is the reusable CI/publish workflow.
- `.github/workflows/aio-check-upstream.yml`, `aio-prepare-release.yml`, and
  `aio-publish-release.yml` centralize upstream monitors and release workflows.
- `aio-fleet` CLI validates the manifest, renders thin caller workflows, checks
  workflow drift, and reports fleet status.
- OpenTofu policy under `infra/github` manages public repo metadata, branch
  protection, selected action allowlists, required checks, vulnerability alerts,
  and declared automation secret names.
- `sync-catalog` stages manifest-declared XML/icon assets into
  `awesome-unraid`, with icon-only support for staged launches before XML
  publication.
- Docs describe the architecture, repo onboarding, release model, GitHub IaC,
  and the future GitHub App automation track.

## Commands

```bash
python -m pip install -e ".[dev]"
python -m aio_fleet doctor
python -m aio_fleet status --github --catalog-path ../awesome-unraid
python -m aio_fleet render-workflow sure-aio --ref <aio-fleet-commit-sha>
python -m aio_fleet verify-caller --repo sure-aio --repo-path ../sure-aio --ref <aio-fleet-commit-sha>
python -m aio_fleet validate --all
python -m aio_fleet validate-repo --repo sure-aio --repo-path ../sure-aio
python -m aio_fleet validate-catalog --catalog-path ../awesome-unraid
python -m aio_fleet validate-github --check-secrets
python -m aio_fleet sync-catalog --repo dify-aio --catalog-path ../awesome-unraid --dry-run
python -m aio_fleet sync-boilerplate --repo sure-aio --dry-run
python -m aio_fleet sync-workflows --dry-run --ref <aio-fleet-commit-sha>
```

## Source model

- `unraid-aio-template`: bootstrap template for new AIO repos.
- App repos: app-specific runtime, Docker image, tests, generated XML, docs.
- `awesome-unraid`: Community Apps catalog output.
- `aio-fleet`: shared fleet control plane.

## Case-study angle

This repo is intentionally public. It demonstrates how a growing homelab
packaging fleet can move from copied repo scaffolding to reusable CI,
manifest-driven repo metadata, drift detection, and eventually GitHub
infrastructure as code.

## Docs

- [Architecture](docs/architecture.md)
- [Repository onboarding](docs/onboarding.md)
- [Release model](docs/release-model.md)
- [Required checks](docs/required-checks.md)
- [Dify launch gate](docs/dify-launch-gate.md)
- [GitHub App automation track](docs/github-app.md)
- [Content series outline](docs/content-series.md)
