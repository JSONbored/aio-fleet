# GitHub App Automation Track

The current fleet automation uses repository secrets such as
`AIO_FLEET_BOT_TOKEN` for generated pull requests that need branch-protection
checks to run. This works, but a GitHub App is the better long-term control-plane
credential because it is scoped, auditable, installable per repository, and
rotatable without sharing a personal token.

## Future App Scope

Start with the smallest app permission set that supports the fleet jobs:

- Metadata: read
- Contents: write
- Pull requests: write
- Checks: write
- Actions: read

Only add broader administration permissions if the GitHub App later owns repo
settings through the OpenTofu layer:

- Administration: write
- Rulesets: write

Do not grant secret-read permissions. GitHub does not expose secret values, and
the fleet should only validate secret names/presence.

## Current Control-Plane Support

The control-plane workflows resolve automation credentials through
`aio_fleet.github_app`:

- If `AIO_FLEET_APP_ID`, `AIO_FLEET_APP_INSTALLATION_ID`, and
  `AIO_FLEET_APP_PRIVATE_KEY` are present, `aio-fleet` mints a short-lived
  installation token.
- If app credentials are absent, release/catalog paths may fall back to existing
  token secrets such as `AIO_FLEET_BOT_TOKEN` while the App path is brought
  online.
- The fallback stays in place until generated catalog and release PRs are proven
  to run required checks and remain mergeable under branch protection with the
  app identity.

Release PR creation should not use `RELEASE_TOKEN` as a generic fallback. That
token can be valid for release/tag operations while still lacking pull-request
write access. Prepare-release workflows should prefer a GitHub App token, then
`AIO_FLEET_BOT_TOKEN`, then the caller job `GITHUB_TOKEN` when no stronger
automation identity is configured.

Check-runs are stricter than release PRs. The required fleet check is created
by `aio-fleet check run`, and the long-term path is a GitHub App installation
token with Checks write access. The local `AIO_FLEET_CHECK_TOKEN` fallback is
only for controlled operator use while bringing the App online.

GitHub's Checks API requires this app-shaped path for creating check-runs:
OAuth apps and authenticated users can view checks, but creating check-runs is
the GitHub App control-plane boundary. That is why the branch-protection
migration waits until the App identity posts a real `aio-fleet / required`
check on an app PR.

Branch protection should require the `aio-fleet / required` check from the
GitHub App's app ID, not just a matching check name. The GitHub branch
protection API exposes this as `required_status_checks.checks[].app_id`, and
`aio-fleet validate-github` fails when the required check is present but tied to
the wrong producer. This prevents a same-name workflow or status from
accidentally satisfying the fleet gate.

Required signed commits stay enabled. Generated commits use the GitHub App
contents API with no custom author, committer, or signature fields so GitHub can
apply bot signature verification. `aio-fleet` then checks the commit API and
fails before PR creation if GitHub reports `verified=false`.

Do not use GitHub rebase-merge on protected AIO repos. GitHub documents that
its rebase-merge path rewrites commits and cannot sign those rewritten commits.
With required signed commits enabled, the secure merge methods are squash or
merge commit through GitHub's signed web flow, or a local signed merge pushed by
an authorized maintainer.

GHCR package publishing is separate from GitHub App check-runs. Use
`AIO_FLEET_GHCR_TOKEN` for the central publish identity.

GitHub Container Registry still expects either workflow-scoped `GITHUB_TOKEN`
permission for the repository/package relationship or a classic package token.
For the central build path, `AIO_FLEET_GHCR_TOKEN` is the explicit operator
credential until a narrower App-only package path is proven.

The private key secret should contain the PEM text. Escaped `\n` sequences are
accepted so the value can be stored in GitHub Secrets without preserving literal
newlines.

## Migration Path

1. Keep `AIO_FLEET_BOT_TOKEN` in place while the workflow and catalog paths stay
   stable.
2. Create the GitHub App with the minimal permission set above.
3. Install it only on active fleet repos and `awesome-unraid`.
4. Add `AIO_FLEET_APP_ID`, `AIO_FLEET_APP_INSTALLATION_ID`, and
   `AIO_FLEET_APP_PRIVATE_KEY` to `aio-fleet`.
5. Verify generated PRs and catalog syncs pass required checks using the app
   identity.
6. Run `aio-fleet poll --create-checks --dry-run`, then a real Sure PR check.
7. Update branch protection to require only `aio-fleet / required` from the
   GitHub App app ID.
8. Run `aio-fleet cleanup-repo --verify`, or `aio-fleet cleanup-repo --fix --verify`
   when removing known retired shared files.
9. Remove PAT secrets after the GitHub App path is stable.

The GitHub App is now the intended control-plane identity; PAT secrets are
temporary fallbacks for any release or registry edge cases that still need
operator verification.
