# Release Model

The fleet keeps release ownership in the app repos.

App repos publish from `main` after required validation and integration gates pass. Formal changelog entries and GitHub Releases remain release-driven, not automatic for every merge.

## App Repos

Most app repos use one of two publish profiles:

- `upstream-aio-track`: wrapper tags are normalized as `<upstream>-aio-v<revision>`.
- `changelog-version`: wrapper tags follow the changelog version exactly.

Publish jobs still require:

- push to `main`;
- a publish-related change;
- successful integration tests.

The reusable `aio-build.yml` owns those publish-gate decisions centrally. App
repos pass only their publish profile and app-specific path exceptions.

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
