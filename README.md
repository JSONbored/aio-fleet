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
- Docs describe the architecture, repo onboarding, release model, and future
  Terraform/Copier layers.

## Commands

```bash
python -m pip install -e ".[dev]"
python -m aio_fleet doctor
python -m aio_fleet status --github
python -m aio_fleet render-workflow sure-aio --ref <aio-fleet-commit-sha>
python -m aio_fleet validate --all
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
- [Content series outline](docs/content-series.md)
