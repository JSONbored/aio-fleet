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

- `upstream-aio-track`: wrapper tags are normalized as `<upstream>-aio-v<revision>`.
- `changelog-version`: wrapper tags follow the changelog version exactly.

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
human review. After that PR merges, normal control-plane validation and publish
rules apply.

`aio-fleet registry publish` and `aio-fleet registry verify` compute the tag set
from `.aio-fleet.yml` and the release commit.

The `Registry Audit` workflow runs read-only verification for every active repo
on a schedule. Scheduled runs report missing Docker Hub or GHCR tags in the job
summary without blocking unrelated control-plane checks; manual runs can set
`fail_on_missing` to make missing tags fail the workflow.

Central release commands:

```bash
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
