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
- Checks: read
- Actions: read

Only add broader administration permissions if the GitHub App later owns repo
settings through the OpenTofu layer:

- Administration: write
- Rulesets: write

Do not grant secret-read permissions. GitHub does not expose secret values, and
the fleet should only validate secret names/presence.

## Current Workflow Support

The reusable workflows already resolve automation credentials through
`aio_fleet.github_app`:

- If `AIO_FLEET_APP_ID`, `AIO_FLEET_APP_INSTALLATION_ID`, and
  `AIO_FLEET_APP_PRIVATE_KEY` are present, the workflow mints a short-lived
  installation token.
- If app credentials are absent, the workflow falls back to existing token
  secrets such as `AIO_FLEET_BOT_TOKEN`, `SYNC_TOKEN`, `RELEASE_TOKEN`, or the
  built-in `GITHUB_TOKEN`, depending on the workflow.
- The fallback stays in place until generated catalog and release PRs are proven
  to run required checks and remain mergeable under branch protection with the
  app identity.

The private key secret should contain the PEM text. Escaped `\n` sequences are
accepted so the value can be stored in GitHub Secrets without preserving literal
newlines.

## Migration Path

1. Keep `AIO_FLEET_BOT_TOKEN` in place while the workflow and catalog paths stay
   stable.
2. Create the GitHub App with the minimal permission set above.
3. Install it only on active fleet repos and `awesome-unraid`.
4. Add `AIO_FLEET_APP_ID`, `AIO_FLEET_APP_INSTALLATION_ID`, and
   `AIO_FLEET_APP_PRIVATE_KEY` to the repos that call reusable fleet workflows.
5. Verify generated PRs and catalog syncs pass required checks using the app
   identity.
6. Remove PAT secrets after the GitHub App path is stable.

This is tracked as future work; the app itself is not created in the current
consolidation pass.
