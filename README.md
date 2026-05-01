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
- `export-app-manifest` renders the future app-local `.aio-fleet.yml` contract.
- `check run` creates or updates the future required GitHub App check-run named
  `aio-fleet / required`.
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
python -m aio_fleet debt-report --catalog-path ../awesome-unraid --format markdown
python -m aio_fleet validate-template-common --all
python -m aio_fleet catalog-audit --catalog-path ../awesome-unraid
python -m aio_fleet release-readiness --repo sure-aio --catalog-path ../awesome-unraid
python -m aio_fleet export-app-manifest --repo sure-aio
python -m aio_fleet check run --repo sure-aio --sha <commit-sha> --event pull_request --dry-run
python -m aio_fleet infra doctor --skip-tofu
python -m aio_fleet onboard-repo --repo example-aio --profile changelog-version --dry-run
python -m aio_fleet support-thread render --repo sure-aio
python -m aio_fleet render-workflow sure-aio --ref <aio-fleet-commit-sha>
python -m aio_fleet verify-caller --repo sure-aio --repo-path ../sure-aio --ref <aio-fleet-commit-sha>
python -m aio_fleet validate --all
python -m aio_fleet validate-derived --repo-path ../sure-aio
python -m aio_fleet validate-repo --repo sure-aio --repo-path ../sure-aio
python -m aio_fleet validate-catalog --catalog-path ../awesome-unraid
python -m aio_fleet validate-github --check-secrets
python -m aio_fleet trunk-audit
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
