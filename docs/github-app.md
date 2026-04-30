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

## Migration Path

1. Keep `AIO_FLEET_BOT_TOKEN` in place while the workflow and catalog paths stay
   stable.
2. Create the GitHub App with the minimal permission set above.
3. Install it only on active fleet repos and `awesome-unraid`.
4. Replace PAT-style workflow secrets with app authentication in `aio-fleet`
   reusable workflows.
5. Remove PAT secrets after generated PRs and catalog syncs pass required checks
   using the app identity.

This is tracked as future work; the app itself is not created in the current
consolidation pass.
