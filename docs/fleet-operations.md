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
the explicit rule that catalog sync follows source validation. They also include
an initial safety summary. The dashboard recomputes that safety state once the
PR, changed files, signed commits, and check results are visible.

Use the safety assessor before merging an upstream PR:

```bash
python -m aio_fleet upstream assess --repo <repo> --pr <number> --format json
```

Notify-only updates can be assessed without a PR:

```bash
python -m aio_fleet upstream assess --repo mem0-aio --format json
```

Submodule-backed repos can still use `strategy: pr` when the monitor declares
the submodule path and ref template. `mem0-aio` is the reference case: the
Dockerfile tracks the upstream Mem0 Python SDK release, while the `openmemory`
gitlink tracks an AIO patch branch in the configured fork. The patch branch
must already exist for the target upstream version before the monitor writes
the source PR; the generated app PR then commits both `Dockerfile` and the
submodule gitlink through the verified GitHub API writer.

Safety levels are deliberately pragmatic:

- `ok`: expected files changed, no obvious template/runtime risk signals, and
  review can proceed.
- `warn`: human review is required for release-note keywords, XML config target
  deltas, or other uncertainty.
- `blocked`: clear failures such as unexpected files, missing manifest-required
  template targets, failed required checks, failed runtime checks, or unverified
  generated commits.
- `manual`: notify-only updates where the packaged app path must be assessed
  before creating a source PR.

`runtime_smoke: deferred-to-main` is intentional for normal PR checks. Heavy
integration tests are configured centrally but run on `main`, release, or manual
dispatch instead of every upstream PR. A real failed runtime check still marks
the assessment `blocked`.

## Fleet Dashboard

The durable operator view is one issue in `JSONbored/aio-fleet`:

```bash
python -m aio_fleet fleet-dashboard update --dry-run
python -m aio_fleet fleet-dashboard update --dry-run --registry --include-activity
python -m aio_fleet fleet-dashboard update --write
```

The issue is labeled `fleet-dashboard` and includes a hidden JSON state block so
scheduled jobs can compare transitions later. It tracks:

- active app repos from `fleet.yml`;
- destination repos such as `awesome-unraid`, rendered as catalog/downstream
  infrastructure rather than app packages;
- rehab repos, rendered as non-blocking onboarding work until they are
  explicitly promoted into the active fleet;
- open PR/issue counts, draft PRs, blocked PRs, stale PRs, clean PRs,
  response-needed issues, and the oldest actionable issue links;
- upstream current/latest versions;
- PR URL and merge state;
- `aio-fleet / required` check state;
- signed/verified commit state;
- safety level, config delta, template impact, and runtime smoke state;
- a `Safety Review` section that summarizes why each update is ok, warn,
  manual, or blocked;
- real registry verification for Docker Hub and GHCR when `--registry` is used;
- release readiness, latest formal release, next `aio.N` candidate, publish
  gaps, and catalog-sync needs;
- control-plane workflow health and dashboard control availability;
- cleanup drift from retired shared app-repo files;
- an overall posture of `green`, `watch`, `action required`, or `blocked`;

The `Controls` section has durable checkbox commands:

- `Rescan dashboard` refreshes the dashboard issue in place.
- `Run upstream monitor` runs the central upstream monitor, opens or updates
  signed source PRs when needed, then refreshes the same dashboard issue.

Both controls reset automatically after the workflow rewrites the issue body.
They should not create dashboard comments.

- source-to-catalog sync queue for destination repos;
- next action for each component.

The same underlying state is available without mutating the dashboard issue:

```bash
python -m aio_fleet fleet-report generate --registry --include-activity --format json
python -m aio_fleet fleet-report schema
python -m aio_fleet fleet-report validate --input fleet-report.json
```

Use `fleet-report generate` for future GitHub Pages, Discord, Raycast, or
GitHub Action surfaces. Those surfaces should consume the versioned report
object and avoid scraping the rendered issue body. The generated report and the
dashboard body are public-text guarded, so local paths, webhook URLs, and similar
operator-only strings are redacted before output.

`nanoclaw-aio` is an active multi-component fleet repo. It participates in
dashboard state, upstream monitoring, registry verification, publish planning,
and `validate --all` like the other active app repos.

## Fleet Doctor

Use the fleet doctor before release work when a failed job would otherwise burn
time on app checkout, Docker/QEMU setup, or registry publishing:

```bash
python -m aio_fleet doctor --publish --cleanup --alerts --format json
python -m aio_fleet doctor --repo nanoclaw-aio --app-checks --format json
python -m aio_fleet doctor --cleanup --check-delete-scope --live-auth --format json
```

The doctor classifies local checkout drift, detached branches, stale branches,
GitHub App check-run permission gaps, missing publish credentials, missing
Docker Hub delete-scope credentials, and alert configuration. The workflow also
writes a minimal control report when the initial check-run bootstrap fails, so
alerts can report `app-check-permission` instead of a generic missing-report
failure.

Missing alert secrets are warnings in the dashboard by default. They only become
failures when a command is run with an explicit required-alerts mode.

## Release And Publish Planning

The release planner answers whether app repos are current, need a formal
wrapper release, are missing registry tags, or need catalog sync after source
validation:

```bash
python -m aio_fleet release plan --all --format json
python -m aio_fleet release plan --repo dify-aio --registry --format json
python -m aio_fleet release reconcile --input release-plan-report.json --format json
python -m aio_fleet release preflight --repo sure-aio --component sure-alpha --mode transaction --format json
python -m aio_fleet release transaction --repo sure-aio --component sure-alpha --sha <release-sha> --dry-run
```

The transaction command is the release checklist entrypoint. It records the
expected SHA, components, release-plan state, preflight findings, failure
classes, and the ordered publish/catalog phases in one JSON report. Write mode
is blocked unless the repo or component is explicitly allowlisted with
`release_transaction.autopilot: true` in `fleet.yml`; absence means dry-run and
operator review only. Write mode also requires a required-check success
attestation, so an allowlist alone is not enough to merge or publish.

Before a real publish, run the same repo-local credential preflight used by the
control-plane workflow:

```bash
python -m aio_fleet registry preflight --mode publish --format json
```

The central control-check runs this preflight before install/test/build work
when publish is requested. Registry publishing is idempotent by default: if all
expected Docker Hub and GHCR tags already verify, `registry publish` reports
`registry=already-present` and exits without pushing. Use `--force` only when a
deliberate rebuild is needed.

For Docker Hub tag cleanup, use a delete-scoped token and verify it before
attempting cleanup:

```bash
export DOCKERHUB_DELETE_TOKEN=...
python -m aio_fleet registry preflight \
  --mode cleanup \
  --image jsonbored/sure-aio-alpha \
  --check-delete-scope \
  --format json
```

`DOCKERHUB_TOKEN` is the publish token. `DOCKERHUB_DELETE_TOKEN` is the cleanup
token and must have Docker Hub tag delete/admin permission. Keeping them
separate prevents normal publish credentials from silently passing preflight and
then failing during cleanup.

`release-due` means there are commits since the latest formal release tag.
`publish-missing` means expected Docker Hub or GHCR tags are absent or
unreachable. `catalog-sync-needed` means a validated source update is ready for
downstream catalog sync. Normal `main` publishes still happen centrally;
formal changelog/GitHub Releases remain release-driven.

For component release rows, the dashboard `Next Commands` section should route
release work through the transaction/preflight entrypoint, not direct registry
or release publishing. A `publish-missing` or `release-due` alpha row should
point at the component transaction:

```bash
python -m aio_fleet release transaction --repo sure-aio --component sure-alpha --sha <release-sha> --dry-run
```

GitHub prerelease publishing must consume the matching control report or an
explicit `--expected-sha`. It refuses to read release metadata if the app
checkout is dirty or if `HEAD` differs from the attested SHA, even if the
workflow reset step already ran. This keeps app-owned test commands from
mutating release notes, tags, or targets before the privileged token is used.

Component template contracts belong in `fleet.yml` under the component
`validation` block. Use that for lane-specific XML rules such as alpha beta
markers, alpha-only envs, and forbidden stable paths or image tags.

## Workflow And Drift Trust

Workflow YAML should stay thin. The hosted workflows now delegate checkout
fanout, summary rendering, registry audit fanout, and dashboard checkout
preparation to tested `aio-fleet workflow ...` CLI jobs. Use the workflow audit
before changing Actions:

```bash
python -m aio_fleet security audit-workflows --format json
```

The audit checks pinned actions, explicit permissions, checkout credential
persistence, strict shell mode, predictable heredocs, and broad token exports.
Dashboard cleanup drift uses the same retired-file policy as
`cleanup-repo --all --verify`.

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
- Missing submodule ref: create or update the configured patch branch, rerun
  upstream monitor, and confirm the app PR includes the gitlink under the
  expected changed paths.
- Missing alert delivery: run `alert doctor`, add the missing secret, then run
  `alert test`.
- Catalog drift: fix the source app repo first, validate it, then run
  `sync-catalog` to open/update an `awesome-unraid` PR.
