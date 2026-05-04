# Release Model

The fleet keeps release source ownership in the app repos, but release mechanics
move to `aio-fleet`.

App repos publish from `main` through the scheduled/manual `aio-fleet`
control-plane poll after required validation passes. Poll runs fan out per repo
so one heavy multi-arch build does not block unrelated app checks. Formal
changelog entries and GitHub Releases remain release-driven, not automatic for
every merge.

## App Repos

Most app repos use one of two publish profiles:

- `upstream-aio-track`: wrapper tags follow `<upstream>-aio.<revision>`.
- `changelog-version`: wrapper tags follow the changelog version exactly.

Every normal `main` publish emits Docker Hub and GHCR tags for:

- `latest`;
- the upstream version, such as `0.7.0`;
- `sha-<commit>`.

Formal release publishes add the exact changelog release tag, such as
`0.7.0-aio.1`. If upstream stays on `0.7.0` but the wrapper, image hardening,
runtime, or template changes need a new package, the next formal release is
`0.7.0-aio.2`. Unraid templates keep using the Docker Hub `latest` tag, so an
image rebuild still changes the remote digest even when the upstream version tag
does not change.

Publish jobs push and verify Docker Hub plus GHCR tags. Docker Hub remains the
template/catalog-preferred image reference; GHCR is a second registry surface
for package availability and operator fallback. Central GHCR publishing should
use `AIO_FLEET_GHCR_TOKEN`.

Publish jobs still require:

- push to `main`;
- a publish-related change;
- successful integration tests.

Upstream bumps are initiated centrally with `aio-fleet upstream monitor`. The
monitor reads provider and digest rules from `.aio-fleet.yml`, updates
version/digest pins when configured for PR strategy, and opens an app repo PR for
human review. Generated upstream commits must be verified/signed. If the writer
cannot produce a verified commit, the branch update fails instead of leaving a
PR that branch protection will later reject. Notify-only monitors never open
PRs; they appear in the fleet dashboard until a human decides whether the
packaged app path is affected. After an upstream PR merges, normal
control-plane validation and publish rules apply.

`aio-fleet registry publish` and `aio-fleet registry verify` compute the tag set
from `.aio-fleet.yml` and the release commit. Docker Hub tag verification uses
the Docker Hub tag API so post-push checks do not consume manifest-pull quota;
GHCR verification continues to use `docker buildx imagetools inspect`.

The `Registry Audit` workflow runs read-only verification for every active repo
on a schedule. Scheduled runs report missing Docker Hub or GHCR tags in the job
summary without blocking unrelated control-plane checks; manual runs can set
`fail_on_missing` to make missing tags fail the workflow.

## Alerting

Alerting is centralized in `aio-fleet`; app repos stay notification-free.
`AIO_FLEET_KUMA_PUSH_URL` drives a Uptime Kuma push heartbeat for fleet health.
`AIO_FLEET_ALERT_WEBHOOK_URL` receives low-noise JSON digests for failures,
missing registry tags, blocked release readiness, and upstream update PRs.
Successes update the Kuma heartbeat but do not send webhook messages unless the
event is an explicit recovery.

Central release commands:

```bash
python -m aio_fleet fleet-dashboard update --dry-run
python -m aio_fleet alert doctor
python -m aio_fleet alert test --dry-run
python -m aio_fleet release status --repo sure-aio
python -m aio_fleet release prepare --repo sure-aio --dry-run
python -m aio_fleet release publish --repo sure-aio --dry-run
python -m aio_fleet registry verify --all --format json
python -m aio_fleet registry verify --repo sure-aio --sha <release-sha>
```

`release prepare` generates a temporary git-cliff config from `aio-fleet`,
updates `CHANGELOG.md`, and renders XML `<Changes>` from the release notes.
App-local `cliff.toml`, `scripts/release.py`, and
`scripts/update-template-changes.py` are retired.

## Generated Templates

Generated-template repos run their generator check before XML validation. This prevents the source repo from publishing a stale Community Apps XML file that later breaks catalog sync.

## Signoz

`signoz-aio` remains component-aware:

- the AIO image and agent image have separate publish lanes;
- both components have upstream monitor entries;
- component path changes decide which image is publish-related;
- agent integration tests build against the matching AIO backend image.
- release and publish commands stay component-aware for `signoz-aio` and
  `signoz-agent`.

This is an explicit fleet exception, not copied hidden logic.

## Catalog Sync

App repos sync CA-facing XML and icon assets into `awesome-unraid` by opening/updating a PR. The catalog repo remains the public CA source of truth.
