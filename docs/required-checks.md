# Required Checks

Use these as the control-plane map for required checks.

## App Repos

App repos should require only:

- `aio-fleet / required`

`aio-fleet` may still post detail checks for validation, tests, registry, and
catalog work, but those are diagnostic. Branch protection should key on the
single required control-plane check.

The check must be created by a GitHub App or another token with Checks write
permission. GitHub documents that check-run write access is available for GitHub
Apps, and the required permission is repository `Checks: write`.

The required check should also be pinned to the GitHub App producer. The live
branch protection API should report `aio-fleet / required` with app ID
`3565017`; `validate-github` treats any other app ID as drift.

## awesome-unraid

Require:

- `validate-catalog`
- `CodeQL`
- `Analyze (actions)`

## Selected Actions

Active repos should keep GitHub Actions restricted to selected actions with SHA
pinning required. App repos should not need selected-action exceptions once
their local workflows are removed. Catalog-specific exceptions, such as
`peter-evans/create-pull-request`, should stay explicit and pinned.
