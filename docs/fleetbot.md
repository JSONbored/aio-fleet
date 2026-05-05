# Fleetbot Extraction Plan

Fleetbot is the reusable product layer proven by `aio-fleet`. `aio-fleet`
stays the Unraid AIO policy pack and public case study; Fleetbot becomes the
generic engine for maintaining many similar repositories from one control plane.

## Core Interfaces

The reusable engine should expose:

- `fleetbot check`: run policy checks for one repo commit.
- `fleetbot monitor`: detect upstream/provider updates.
- `fleetbot dashboard update`: render or update the durable dashboard issue.
- `fleetbot registry verify`: verify image/package tags.
- `fleetbot release readiness`: summarize release blockers.

The first stable report format is the versioned `FleetReport` state emitted by
`aio-fleet fleet-report generate --format json`. The dashboard issue consumes
that same state before rendering Markdown. Discord, Raycast, GitHub Actions,
the GitHub App, and a future web dashboard should all consume the report shape
instead of scraping the rendered issue body or inventing separate models.

Contract helpers:

```bash
python -m aio_fleet fleet-report generate --registry --include-activity --format json
python -m aio_fleet fleet-report schema
python -m aio_fleet fleet-report validate --input fleet-report.json
```

The report includes active repo/component rows, upstream status, safety,
required checks, signed commit state, registry verification, release readiness,
GitHub activity, cleanup drift, control-plane workflow health, alert warnings,
and next actions. New surfaces should add presentation only; they should not
recompute fleet truth independently.

## Product Surfaces

- GitHub Action: thin Marketplace wrapper around the CLI for easy OSS adoption.
- GitHub App: required checks, signed PRs, issue dashboard, and alert routing.
- Discord bot: `/fleet status`, `/fleet updates`, `/fleet blocked`,
  `/fleet repo <name>`, and `/fleet release-ready <name>`.
- Raycast extension: local operator command center for status, PRs, registry
  state, release readiness, and workflow links.
- Web dashboard: later, after CLI/App/Discord usage proves the workflow.

## Public Packaging

Start OSS/self-hosted:

- CLI and manifest schema;
- GitHub Action;
- self-hosted GitHub App mode;
- policy-pack examples, including `aio-unraid`;
- AIO fleet case study and screenshots from the dashboard issue.

Hosted/paid later:

- hosted GitHub App;
- managed dashboard and alert routing;
- private repo support;
- team permissions and audit history;
- custom policy packs;
- managed fleet-consolidation service.

## Wedge

Do not position Fleetbot as a generic developer portal. The sharper wedge is:

> Renovate/Dependabot for repo fleets that need policy, registry, release, and
> operator checks, not just dependency bumps.

Initial users are maintainers of Docker image fleets, template repos, GitHub
Actions, Helm charts, Terraform modules, SDK repos, and internal platform
repos.

## Extraction Gate

Do not split Fleetbot out until `aio-fleet` proves:

- generated upstream PRs are verified/signed;
- the fleet dashboard issue is updated by schedule;
- notify-only updates are visible without PR spam;
- alert delivery works through Kuma and webhook;
- current AIO upstream PRs are repaired or intentionally held.
- dashboard registry and release readiness come from real control-plane checks,
  not placeholders;
- workflow fanout and summaries are CLI-backed instead of large untested YAML
  scripts.
