# Required Checks

Use these as the default required status checks for app repos after they are on
the shared `aio-fleet` workflows.

## App Repos

Require:

- `aio-build / validate-template`
- `aio-build / pinned-actions`
- `aio-build / unit-tests`
- `aio-build / dependency-review`
- `CodeQL` when CodeQL is enabled
- `Analyze (actions)` when CodeQL is enabled
- `Analyze (python)` when CodeQL is enabled

Require `aio-build / integration-tests` only for repos where branch protection
should force Docker integration on every PR. The reusable workflow still gates
publish paths behind integration success on `main`.

## SigNoz

Add `aio-build / agent-integration-tests` after the agent lane is fully settled
as a required check for agent-affecting PRs.

## Do Not Require

- `aio-build / publish`
- `aio-build / publish-agent`
- `aio-build / sync-awesome-unraid`
- `aio-build / extended-integration-tests`

Dify extended integration remains a manual launch/pre-release gate, not a
required status check for every PR.
