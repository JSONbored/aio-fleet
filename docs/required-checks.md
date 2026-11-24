# Required Checks

Use these as the control-plane map for required checks.

## App Repos

App repos should require:

- `aio-fleet / required`

`aio-fleet` may still post detail checks for validation, tests, registry, and
catalog work, but those are diagnostic. Branch protection should key on the
required control-plane check.

The check must be created by a GitHub App or another token with Checks write
permission. GitHub documents that check-run write access is available for GitHub
Apps, and the required permission is repository `Checks: write`.

The required check should also be pinned to the GitHub App producer. The live
branch protection API should report `aio-fleet / required` with app ID
`3565017`; `validate-github` treats any other app ID as drift.

## aio-fleet

Require:

- `test`
- `infra`
- `Analyze (actions)`
- `Analyze (python)`

The GitHub Actions contexts should be pinned to app ID `15368`.

## awesome-unraid

Require:

- `validate-catalog`
- `CodeQL`
- `Analyze (actions)`

Pin the GitHub Actions contexts to app ID `15368` and the standalone `CodeQL` check
to app ID `57789`.

## unraid-aio-template

Require `aio-fleet / required` from app ID `3565017`. The repo is public and uses the same
managed branch-protection/ruleset posture as active app repos so new AIO repos
start from a protected bootstrap baseline.

## Selected Actions

Active repos should keep GitHub Actions restricted to selected actions with SHA
pinning required. App repos should not need selected-action exceptions once
their local workflows are removed. Catalog-specific exceptions, such as
`peter-evans/create-pull-request`, should stay explicit and pinned.
