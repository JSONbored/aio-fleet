# Fleet Operations

`aio-fleet` is the single operator surface for upstream updates, required
checks, registry state, release readiness, and alerting across the active AIO
repos. App repos remain the source of truth for Dockerfiles, runtime code,
source XML, docs, and app-specific tests. `awesome-unraid` is downstream only:
catalog sync follows validated source repo changes.

## Upstream Updates

Scheduled upstream monitoring runs from the `AIO Fleet Control Plane` workflow.
Manual checks use the same path:

```bash
python -m aio_fleet upstream monitor --all --dry-run --format json
python -m aio_fleet upstream monitor --all --write --create-pr --post-check
```

`strategy: pr` opens or updates one source repo PR for the generated upstream
branch. The commit must be verified before the PR is considered actionable
under branch protection. `strategy: notify` never opens a PR; it appears in the
fleet dashboard for manual triage.

Generated PRs should contain upstream release links, changed source paths, and
the explicit rule that catalog sync follows source validation.

## Fleet Dashboard

The durable operator view is one issue in `JSONbored/aio-fleet`:

```bash
python -m aio_fleet fleet-dashboard update --dry-run
python -m aio_fleet fleet-dashboard update --write
```

The issue is labeled `fleet-dashboard` and includes a hidden JSON state block so
scheduled jobs can compare transitions later. It tracks:

- upstream current/latest versions;
- PR URL and merge state;
- `aio-fleet / required` check state;
- signed/verified commit state;
- registry and release readiness placeholders;
- next action for each component.

Missing alert secrets are warnings in the dashboard by default. They only become
failures when a command is run with an explicit required-alerts mode.

## Alerting

App repos stay notification-free. `aio-fleet` owns alert routing.

```bash
python -m aio_fleet alert doctor
python -m aio_fleet alert doctor --require-alerts
python -m aio_fleet alert test --dry-run
```

Secrets:

- `AIO_FLEET_KUMA_PUSH_URL`: Uptime Kuma push heartbeat.
- `AIO_FLEET_ALERT_WEBHOOK_URL`: JSON or text webhook for rich digest alerts.

Alerts should stay low-noise: new update, new blocker, new failure, recovery,
missing registry tags, or blocked release readiness. Routine successes update
the heartbeat without sending a separate webhook digest.

## Troubleshooting

- Unsigned upstream PR: regenerate the branch through the verified writer or
  use local `AIO_FLEET_UPSTREAM_COMMIT_MODE=git-signed` only when a trusted
  signing key is available, then verify with the commit API.
- Required check spoof/drift: run `python -m aio_fleet validate-github`; app
  repos should require `aio-fleet / required` from the configured GitHub App
  app ID.
- Signed-commit merge failure: do not use GitHub rebase-merge. Use squash,
  merge commit, or a local signed merge from an authorized maintainer.
- Stale upstream PR: rerun upstream monitor; older generated upstream PRs are
  closed as superseded when a newer generated branch is created.
- Notify-only update: review the upstream release manually, decide whether the
  packaged app path is affected, then create a source repo PR only if needed.
- Missing alert delivery: run `alert doctor`, add the missing secret, then run
  `alert test`.
- Catalog drift: fix the source app repo first, validate it, then run
  `sync-catalog` to open/update an `awesome-unraid` PR.
