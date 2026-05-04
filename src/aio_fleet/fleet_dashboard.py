from __future__ import annotations

import json
import os
import subprocess  # nosec B404
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aio_fleet.checks import CHECK_NAME
from aio_fleet.manifest import FleetManifest, RepoConfig
from aio_fleet.upstream import UpstreamMonitorResult, monitor_repo, upstream_branch

DASHBOARD_LABEL = "fleet-dashboard"
DASHBOARD_TITLE = "Fleet Update Dashboard"
STATE_START = "<!-- aio-fleet-dashboard-state"
STATE_END = "-->"


@dataclass(frozen=True)
class DashboardIssueResult:
    action: str
    number: int | None
    url: str


def dashboard_report(
    manifest: FleetManifest,
    *,
    include_registry: bool = False,
    issue_repo: str = "JSONbored/aio-fleet",
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = env or os.environ
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    rows: list[dict[str, Any]] = []
    warnings = alert_warnings(env)
    for repo in manifest.repos.values():
        if repo.publish_profile == "template":
            rows.append(
                {
                    "repo": repo.name,
                    "component": "template",
                    "current": "",
                    "latest": "",
                    "strategy": "manual",
                    "update": False,
                    "pr": "",
                    "check": "not-applicable",
                    "signed": "not-applicable",
                    "registry": "manual",
                    "release": "manual",
                    "next_action": "manual template baseline",
                }
            )
            continue
        try:
            results = monitor_repo(repo, write=False)
        except Exception as exc:
            rows.append(
                {
                    "repo": repo.name,
                    "component": "aio",
                    "current": "",
                    "latest": "",
                    "strategy": "unknown",
                    "update": False,
                    "pr": "",
                    "check": "unknown",
                    "signed": "unknown",
                    "registry": "not-run",
                    "release": "unknown",
                    "next_action": f"upstream monitor failed: {exc}",
                }
            )
            continue
        for result in results:
            rows.append(_dashboard_row(repo, result, include_registry=include_registry))
    state = {
        "schema_version": 1,
        "generated_at": generated_at,
        "issue_repo": issue_repo,
        "warnings": warnings,
        "rows": rows,
    }
    return {"state": state, "body": render_dashboard(state)}


def render_dashboard(state: dict[str, Any]) -> str:
    rows = list(state.get("rows", []))
    warnings = list(state.get("warnings", []))
    attention = [
        row
        for row in rows
        if row.get("update") or str(row.get("next_action", "")).startswith("upstream")
    ]
    lines = [
        "# Fleet Update Dashboard",
        "",
        f"Last updated: `{state.get('generated_at', '')}`",
        "",
        "> Source repo updates start in each `<app>-aio` repo. `awesome-unraid` catalog sync follows validated source changes.",
        "",
    ]
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    lines.extend(["## Attention", ""])
    if attention:
        for row in attention:
            lines.append(
                "- `{repo}:{component}` {current} -> {latest}: {next_action}".format(
                    **row
                )
            )
    else:
        lines.append("- No upstream updates currently require action.")
    lines.extend(
        [
            "",
            "## Fleet State",
            "",
            "| Repo | Component | Current | Latest | Strategy | PR | Check | Signed | Registry | Release | Next |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            "| {repo} | {component} | {current} | {latest} | {strategy} | {pr} | {check} | {signed} | {registry} | {release} | {next_action} |".format(
                **{key: _cell(row.get(key, "")) for key in row}
            )
        )
    lines.extend(
        [
            "",
            STATE_START,
            json.dumps(state, indent=2, sort_keys=True),
            STATE_END,
            "",
        ]
    )
    return "\n".join(lines)


def alert_warnings(env: dict[str, str]) -> list[str]:
    warnings: list[str] = []
    if not env.get("AIO_FLEET_KUMA_PUSH_URL"):
        warnings.append(
            "AIO_FLEET_KUMA_PUSH_URL is not configured; heartbeat alerts are disabled."
        )
    if not env.get("AIO_FLEET_ALERT_WEBHOOK_URL"):
        warnings.append(
            "AIO_FLEET_ALERT_WEBHOOK_URL is not configured; rich digest alerts are disabled."
        )
    return warnings


def upsert_dashboard_issue(
    *,
    issue_repo: str,
    body: str,
    title: str = DASHBOARD_TITLE,
    label: str = DASHBOARD_LABEL,
    dry_run: bool,
) -> DashboardIssueResult:
    existing = _find_dashboard_issue(issue_repo, label=label)
    if dry_run:
        return DashboardIssueResult(
            action="would-update" if existing else "would-create",
            number=int(existing["number"]) if existing else None,
            url=str(existing.get("url", "")) if existing else "",
        )
    _ensure_label(issue_repo, label=label)
    if existing:
        number = int(existing["number"])
        _run(
            [
                "gh",
                "issue",
                "edit",
                str(number),
                "--repo",
                issue_repo,
                "--title",
                title,
                "--body",
                body,
                "--add-label",
                label,
            ]
        )
        return DashboardIssueResult(
            action="updated",
            number=number,
            url=str(existing.get("url", "")),
        )
    created = _run(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            issue_repo,
            "--title",
            title,
            "--body",
            body,
            "--label",
            label,
        ]
    )
    url = created.stdout.strip()
    return DashboardIssueResult(
        action="created",
        number=_issue_number_from_url(url),
        url=url,
    )


def _dashboard_row(
    repo: RepoConfig,
    result: UpstreamMonitorResult,
    *,
    include_registry: bool,
) -> dict[str, Any]:
    branch = upstream_branch(repo, [result]) if result.strategy == "pr" else ""
    pr = _open_pr(repo, branch) if branch else None
    pr_url = str(pr.get("url", "")) if pr else ""
    pr_label = f"[#{pr['number']}]({pr_url})" if pr and pr_url else ""
    check = (
        _check_state(pr)
        if pr
        else ("not-needed" if not result.updates_available else "missing")
    )
    signed = (
        _signed_state(repo, pr)
        if pr
        else ("not-needed" if not result.updates_available else "missing")
    )
    registry = "not-run"
    if include_registry:
        registry = "current-not-verified"
    release = "after-merge" if result.updates_available else "current"
    next_action = _next_action(result, pr=pr, signed=signed, check=check)
    return {
        "repo": repo.name,
        "component": result.component,
        "current": result.current_version,
        "latest": result.latest_version,
        "strategy": result.strategy,
        "update": result.updates_available,
        "pr": pr_label,
        "check": check,
        "signed": signed,
        "registry": registry,
        "release": release,
        "next_action": next_action,
    }


def _next_action(
    result: UpstreamMonitorResult,
    *,
    pr: dict[str, Any] | None,
    signed: str,
    check: str,
) -> str:
    if not result.updates_available:
        return "none"
    if result.strategy == "notify":
        return "manual triage; notify-only strategy"
    if not pr:
        return "open signed source repo PR"
    merge_state = str(pr.get("mergeStateStatus") or "").lower()
    if signed != "verified":
        return "regenerate/update PR with verified signed commit"
    if check != "success":
        return f"wait for central check: {check}"
    if merge_state in {"behind", "blocked", "dirty"}:
        return f"resolve PR state: {merge_state}"
    return "human review and merge"


def _open_pr(repo: RepoConfig, branch: str) -> dict[str, Any] | None:
    result = _run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo.github_repo,
            "--head",
            branch,
            "--base",
            "main",
            "--json",
            "number,url,headRefOid,mergeStateStatus,statusCheckRollup",
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return data[0] if data else None


def _check_state(pr: dict[str, Any] | None) -> str:
    if not pr:
        return "missing"
    for check in pr.get("statusCheckRollup", []):
        if isinstance(check, dict) and check.get("name") == CHECK_NAME:
            if check.get("status") == "COMPLETED":
                return str(check.get("conclusion", "unknown")).lower()
            return str(check.get("status", "unknown")).lower()
    return "missing"


def _signed_state(repo: RepoConfig, pr: dict[str, Any] | None) -> str:
    if not pr:
        return "missing"
    number = str(pr.get("number") or "")
    if not number:
        return "missing"
    result = _run(
        [
            "gh",
            "api",
            f"repos/{repo.github_repo}/pulls/{number}/commits",
            "--paginate",
        ],
        check=False,
    )
    if result.returncode != 0:
        return "unknown"
    try:
        commits = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return "unknown"
    if not isinstance(commits, list) or not commits:
        return "missing"
    reasons: list[str] = []
    for commit in commits:
        if not isinstance(commit, dict):
            continue
        verification = commit.get("commit", {}).get("verification", {})
        if isinstance(verification, dict) and verification.get("verified") is True:
            continue
        reasons.append(str(verification.get("reason") or "unverified"))
    if not reasons:
        return "verified"
    return ",".join(sorted(set(reasons)))


def _find_dashboard_issue(issue_repo: str, *, label: str) -> dict[str, Any] | None:
    result = _run(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            issue_repo,
            "--state",
            "open",
            "--label",
            label,
            "--json",
            "number,title,url",
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        issues = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return issues[0] if issues else None


def _ensure_label(issue_repo: str, *, label: str) -> None:
    _run(
        [
            "gh",
            "label",
            "create",
            label,
            "--repo",
            issue_repo,
            "--color",
            "0969da",
            "--description",
            "Central AIO fleet update dashboard",
        ],
        check=False,
    )


def _issue_number_from_url(url: str) -> int | None:
    try:
        return int(url.rstrip("/").rsplit("/", 1)[1])
    except (IndexError, ValueError):
        return None


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # nosec B603
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{' '.join(command)} failed: {detail}")
    return result


def _cell(value: object) -> str:
    text = str(value).replace("\n", " ").replace("|", "\\|").strip()
    return text or "-"
