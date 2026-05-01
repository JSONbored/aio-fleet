# Releases

`{{ repo }}` uses upstream-version-plus-AIO-revision releases such as `vX.Y.Z-aio.1`.

Stable upstream version monitoring and upstream image digest monitoring are separate concerns. Version bumps should open explicit upstream-update PRs, while digest-only refreshes should flow through normal dependency update automation.

## Version format

- first wrapper release for upstream `vX.Y.Z`: `vX.Y.Z-aio.1`
- second wrapper-only release on the same upstream: `vX.Y.Z-aio.2`
- first wrapper release after upgrading upstream: `vA.B.C-aio.1`

## Published image tags

Every `main` build publishes:

- `latest`
- the exact pinned upstream version
- `sha-<commit>`

Release commits also publish the immutable packaging-line tag derived from the changelog release version. Ordinary `main` pushes do not overwrite that release tag.

The Unraid template uses Docker Hub image names for Community Applications metadata. Publish jobs require Docker Hub credentials and push the same tag set there directly.

## Release flow

1. Trigger **Prepare Release / {{ release_name }}** from `main`.
2. The workflow computes the next upstream-aligned AIO version, updates `CHANGELOG.md`, syncs the XML `<Changes>` block, and opens a release PR.
3. Review and merge that PR into `main`.
4. Wait for the `CI` run on the release target commit to finish green. That same `main` push also publishes the updated package tags automatically.
5. Trigger **Publish Release / {{ release_name }}** from `main`.
6. The workflow verifies CI on the exact release target commit, creates the Git tag if needed, and publishes the GitHub Release.
