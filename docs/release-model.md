# Release Model

The fleet keeps release source ownership in the app repos, but release mechanics
move to `aio-fleet`.

App repos publish from `main` after required validation and integration gates pass. Formal changelog entries and GitHub Releases remain release-driven, not automatic for every merge.

## App Repos

Most app repos use one of two publish profiles:

- `upstream-aio-track`: wrapper tags are normalized as `<upstream>-aio-v<revision>`.
- `changelog-version`: wrapper tags follow the changelog version exactly.

Publish jobs push and verify Docker Hub plus GHCR tags. Docker Hub remains the
template/catalog-preferred image reference; GHCR is a second registry surface
for package availability and operator fallback. Central GHCR publishing should
use `AIO_FLEET_GHCR_TOKEN`; app-repo reusable workflow callers may fall back to
repo `GITHUB_TOKEN` while the build still runs in the app repository.

Publish jobs still require:

- push to `main`;
- a publish-related change;
- successful integration tests.

During the transitional layer, reusable `aio-build.yml` owns those publish-gate
decisions centrally. In the control-plane layer, `aio-fleet registry publish`
and `aio-fleet registry verify` compute the same tag set from `.aio-fleet.yml`
and the release commit.

Central release commands:

```bash
python -m aio_fleet release status --repo sure-aio
python -m aio_fleet release prepare --repo sure-aio --dry-run
python -m aio_fleet release publish --repo sure-aio --dry-run
python -m aio_fleet registry verify --repo sure-aio --sha <release-sha>
```

`release prepare` generates a temporary git-cliff config from `aio-fleet`,
updates `CHANGELOG.md`, and renders XML `<Changes>` from the release notes.
Once this path is the active release path, app-local `cliff.toml`,
`scripts/release.py`, and `scripts/update-template-changes.py` are retired.

Prepare-release workflows check out `aio-fleet` helpers outside the caller
repository workspace. Helper checkouts must never appear as `.aio-fleet`
gitlinks in generated release branches.

## Generated Templates

Generated-template repos run their generator check before XML validation. This prevents the source repo from publishing a stale Community Apps XML file that later breaks catalog sync.

## Signoz

`signoz-aio` remains component-aware:

- the AIO image and agent image have separate publish lanes;
- component path changes decide which image is publish-related;
- agent integration tests build against the matching AIO backend image.
- release and publish workflows stay separate for `signoz-aio` and
  `signoz-agent`, but both call the same reusable release workflows.

This is an explicit fleet exception, not copied hidden logic.

## Catalog Sync

App repos sync CA-facing XML and icon assets into `awesome-unraid` by opening/updating a PR. The catalog repo remains the public CA source of truth.
