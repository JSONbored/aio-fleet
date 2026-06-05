# AIO Fleet

Central control plane for the JSONbored Unraid AIO fleet.

`aio-fleet` keeps the shared CI, release, validation, and drift-management logic
out of individual app repos. App repos stay focused on their runtime wrapper,
Dockerfile, Unraid template, docs, and tests. This repo owns the fleet contract.

## Current slice

- `fleet.yml` is the canonical manifest for app repo metadata and exceptions.
- Publish jobs push and verify Docker Hub plus GHCR tags while Unraid templates
  continue to prefer Docker Hub image metadata.
- `aio-fleet` CLI validates the manifest, reports fleet status, and runs the
  Python-driven control-plane path that replaced app-local workflow callers.
- `export-app-manifest` renders the future app-local `.aio-fleet.yml` contract.
- `poll`, `control-check`, and `check run` scan app repos and create or update
  the required GitHub App check-run named `aio-fleet / required`; `Superagent
Security Scan` and `Contributor trust` are the companion blocking review gates.
- Scheduled/manual poll runs discover missing app checks centrally, then fan out
  per repo so validation and publish work scales with the fleet instead of
  blocking behind one serial app build.
- `upstream monitor` detects manifest-declared upstream version/digest changes
  and can open or update app repo PRs with the GitHub App identity. Generated
  commits are verified before the PR is considered actionable.
- `fleet-dashboard update` maintains one central issue in `aio-fleet` for
  upstream updates, PR/issue activity, signed commit state, required checks,
  real registry verification, release readiness, cleanup drift, control-plane
  health, posture, and next actions.
- App test dependencies are installed from `aio-fleet[app-tests]`; app repos no
  longer carry shared `requirements-dev.txt` files.
- `registry verify/publish` computes and verifies Docker Hub plus GHCR tags from
  the manifest and release state.
- The `Registry Audit` workflow runs read-only Docker Hub/GHCR verification on
  a schedule and can be manually enforced with `fail_on_missing`.
- Central alerting sends a Uptime Kuma fleet heartbeat plus optional low-noise
  JSON webhook digests for upstream updates, failures, and missing registry
  tags.
- `release status/prepare/publish` uses central changelog and XML `<Changes>`
  rendering instead of app-local release scripts.
- `trunk run` overlays the central `.trunk` config into scratch checkouts so
  app repos can drop local Trunk config after the check-run migration is proven.
- `hooks install` installs local pre-commit/pre-push hooks that run the central
  Trunk overlay and repo validation before changes leave a checkout.
- `cleanup-repo` verifies and, with `--fix`, removes retired app-local shared
  files.
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
uv sync --extra dev
uv run aio-fleet doctor
uv run aio-fleet status --github --catalog-path ../awesome-unraid
uv run aio-fleet debt-report --catalog-path ../awesome-unraid --format markdown
uv run aio-fleet validate-template-common --all
uv run aio-fleet catalog-audit --catalog-path ../awesome-unraid
uv run aio-fleet catalog-workflow --catalog-path ../awesome-unraid --check
uv run aio-fleet catalog-changelog --catalog-path ../awesome-unraid --check
uv run aio-fleet release-readiness --repo sure-aio --catalog-path ../awesome-unraid
uv run aio-fleet poll --format json
uv run aio-fleet control-check --repo sure-aio --sha <commit-sha> --event pull_request --dry-run
uv run aio-fleet upstream monitor --all --dry-run
uv run aio-fleet upstream monitor --repo sure-aio --write --create-pr --post-check
uv run aio-fleet fleet-dashboard update --dry-run --registry --include-activity
uv run aio-fleet fleet-report generate --registry --include-activity --format json
uv run aio-fleet fleet-report closeout --format json
uv run aio-fleet fleet-report schema
uv run aio-fleet fleet-report validate --input fleet-report.json
uv run aio-fleet registry verify --repo sure-aio --sha <commit-sha> --dry-run --verbose
uv run aio-fleet alert doctor
uv run aio-fleet alert test --dry-run
uv run aio-fleet alert send --event registry-audit --report-json registry-report.json --dry-run
uv run aio-fleet release status --repo sure-aio
uv run aio-fleet release plan --all --format json
uv run aio-fleet release prepare --repo sure-aio --dry-run
uv run aio-fleet release publish --repo sure-aio --dry-run
uv run aio-fleet registry verify --all --format json
uv run aio-fleet cleanup-repo --repo sure-aio --verify
uv run aio-fleet cleanup-repo --repo sure-aio --fix --verify
uv run aio-fleet security audit-workflows --format json
uv run aio-fleet promote-rehab --repo nanoclaw-aio --dry-run --format json
uv run aio-fleet trunk run --repo sure-aio --no-fix
uv run aio-fleet trunk run --repo sure-aio --local --fix
uv run aio-fleet hooks install --all --include-destinations
uv run aio-fleet export-app-manifest --repo sure-aio
uv run aio-fleet import-app-manifest --path ../sure-aio/.aio-fleet.yml
uv run aio-fleet check run --repo sure-aio --sha <commit-sha> --event pull_request --dry-run
uv run aio-fleet infra doctor --skip-tofu
uv run aio-fleet onboard-repo --repo example-aio --profile changelog-version --dry-run
uv run aio-fleet onboard-repo --repo nanoclaw-aio --shape multi-component --format json
uv run aio-fleet onboard-repo --repo penpot-aio --shape multi-component --format json
uv run aio-fleet support-thread render --repo sure-aio
uv run aio-fleet validate --all
uv run aio-fleet validate-derived --repo-path ../sure-aio
uv run aio-fleet validate-repo --repo sure-aio --repo-path ../sure-aio
uv run aio-fleet validate-catalog --catalog-path ../awesome-unraid
uv run aio-fleet validate-github --check-secrets
uv run aio-fleet trunk-audit
uv run aio-fleet sync-catalog --repo dify-aio --catalog-path ../awesome-unraid --dry-run
```

## Source model

- `unraid-aio-template`: bootstrap template for new AIO repos.
- App repos: app-specific runtime, Docker image, tests, generated XML, docs.
- `awesome-unraid`: Community Apps catalog output.
- `aio-fleet`: shared fleet control plane.

## Case-study angle

This repo is intentionally public. It demonstrates how a growing homelab
packaging fleet can move from copied repo scaffolding to manifest-driven
control-plane automation, drift detection, and GitHub infrastructure as code.

## Docs

- [Architecture](docs/architecture.md)
- [Fleet operations](docs/fleet-operations.md)
- [Repository onboarding](docs/onboarding.md)
- [Release model](docs/release-model.md)
- [Required checks](docs/required-checks.md)
- [Dify launch gate](docs/dify-launch-gate.md)
- [GitHub App automation track](docs/github-app.md)
- [Fleetbot extraction plan](docs/fleetbot.md)
- [Content series outline](docs/content-series.md)
