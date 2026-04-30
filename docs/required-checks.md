# Required Checks

Use these as the default required status checks after repos are on the shared
`aio-fleet` workflows.

## AIO App Repos

Require:

- `aio-build / validate-template`
- `aio-build / pinned-actions`
- `aio-build / unit-tests`
- `aio-build / integration-tests`
- `aio-build / dependency-review`
- `CodeQL`
- `Analyze (actions)`
- `Analyze (python)`

Add language-specific CodeQL jobs only where they actually exist. Today that
means `mem0-aio` also requires `Analyze (javascript-typescript)`.

## unraid-aio-template

Require:

- `aio-build / validate-template`
- `aio-build / pinned-actions`
- `aio-build / unit-tests`
- `aio-build / integration-tests`
- `aio-build / dependency-review`

Do not require CodeQL checks unless CodeQL is enabled for this repo.

## awesome-unraid

Require:

- `validate-catalog`
- `CodeQL`
- `Analyze (actions)`

## SigNoz

Do not require `aio-build / agent-integration-tests` yet. Add it only after the
agent lane is fully settled as a required check for agent-affecting PRs.

## Do Not Require

- `aio-build / publish`
- `aio-build / publish-agent`
- `aio-build / sync-awesome-unraid`
- `aio-build / extended-integration-tests`

Dify extended integration remains a manual launch/pre-release gate, not a
required status check for every PR.

## Selected Actions

Active repos should keep GitHub Actions restricted to selected actions with SHA
pinning required. The reusable fleet workflows are allowlisted with:

- `JSONbored/aio-fleet/.github/workflows/aio-*.yml@*`

Because SHA pinning is required, app repos still call each reusable workflow at
a full commit SHA. The wildcard only removes the repetitive GitHub settings
update that would otherwise be needed after every `aio-fleet` commit.
