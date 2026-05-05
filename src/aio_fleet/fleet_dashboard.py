from __future__ import annotations

import base64
import json
import os
import re
import subprocess  # nosec B404
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aio_fleet.checks import CHECK_NAME
from aio_fleet.cleanup import cleanup_findings
from aio_fleet.manifest import FleetManifest, RepoConfig
from aio_fleet.safety import assess_upstream_pr
from aio_fleet.upstream import UpstreamMonitorResult, monitor_repo, upstream_branch
from aio_fleet.validators import catalog_repo_failures

DASHBOARD_LABEL = "fleet-dashboard"
DASHBOARD_TITLE = "Fleet Update Dashboard"
STATE_START = "<!-- aio-fleet-dashboard-state"
STATE_START_BASE64 = "<!-- aio-fleet-dashboard-state:base64"
STATE_END = "-->"
DASHBOARD_COMMANDS = {
    "rescan": "Rescan dashboard",
    "upstream_monitor": "Run upstream monitor",
}
CHECKED_COMMAND_RE = re.compile(
    r"^-\s+\[[xX]\]\s+(?P<label>.+?)\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class DashboardIssueResult:
    action: str
    number: int | None
    url: str


@dataclass(frozen=True)
class DashboardRepoRef:
    name: str
    github_repo: str
    path: Path
    raw: dict[str, Any]


def dashboard_report(
    manifest: FleetManifest,
    *,
    include_registry: bool = False,
    include_activity: bool = True,
    stale_days: int = 7,
    issue_repo: str = "JSONbored/aio-fleet",
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = env or os.environ
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    warnings = alert_warnings(env)
    active_rows: list[dict[str, Any]] = []
    activity_rows: list[dict[str, Any]] = []

    for repo in manifest.repos.values():
        if include_activity:
            activity_rows.append(repo_activity(repo.name, repo.github_repo, stale_days))
        if repo.publish_profile == "template":
            active_rows.append(_template_row(repo, include_registry=include_registry))
            continue
        try:
            results = monitor_repo(repo, write=False)
        except Exception as exc:
            active_rows.append(_monitor_failure_row(repo, exc))
            continue
        for result in results:
            active_rows.append(
                _dashboard_row(repo, result, include_registry=include_registry)
            )

    destination_rows = [
        _destination_row(
            manifest,
            ref,
            active_rows=active_rows,
            include_activity=include_activity,
            stale_days=stale_days,
        )
        for ref in _dashboard_repo_refs(manifest, "destination_repos")
    ]
    rehab_rows = [
        _rehab_row(ref, include_activity=include_activity, stale_days=stale_days)
        for ref in _dashboard_repo_refs(manifest, "rehab_repos")
    ]
    summary = dashboard_summary(
        active_rows=active_rows,
        activity_rows=activity_rows,
        destination_rows=destination_rows,
        rehab_rows=rehab_rows,
        warnings=warnings,
    )
    state = {
        "schema_version": 2,
        "generated_at": generated_at,
        "issue_repo": issue_repo,
        "warnings": warnings,
        "summary": summary,
        "rows": active_rows,
        "activity": activity_rows,
        "destination_repos": destination_rows,
        "rehab_repos": rehab_rows,
    }
    return {"state": state, "body": render_dashboard(state)}


def dashboard_summary(
    *,
    active_rows: list[dict[str, Any]],
    activity_rows: list[dict[str, Any]],
    destination_rows: list[dict[str, Any]],
    rehab_rows: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    repo_names = {str(row.get("repo", "")) for row in active_rows if row.get("repo")}
    update_rows = [row for row in active_rows if row.get("update")]
    return {
        "active_repos": len(repo_names),
        "destination_repos": len(destination_rows),
        "rehab_repos": len(rehab_rows),
        "upstream_updates": len(update_rows),
        "ready_updates": len([row for row in update_rows if _is_ready_update(row)]),
        "triage_updates": len([row for row in update_rows if _is_triage_update(row)]),
        "blocked_updates": len([row for row in update_rows if _is_blocked_update(row)]),
        "runtime_deferred": len(
            [
                row
                for row in update_rows
                if row.get("runtime_smoke") == "deferred-to-main"
            ]
        ),
        "open_prs": sum(_int(row.get("open_prs")) for row in activity_rows)
        + sum(_int(row.get("open_prs")) for row in destination_rows)
        + sum(_int(row.get("open_prs")) for row in rehab_rows),
        "open_issues": sum(_int(row.get("open_issues")) for row in activity_rows)
        + sum(_int(row.get("open_issues")) for row in destination_rows)
        + sum(_int(row.get("open_issues")) for row in rehab_rows),
        "ready_prs": sum(_int(row.get("clean_prs")) for row in activity_rows)
        + sum(_int(row.get("clean_prs")) for row in destination_rows)
        + sum(_int(row.get("clean_prs")) for row in rehab_rows),
        "blocked_prs": sum(_int(row.get("blocked_prs")) for row in activity_rows)
        + sum(_int(row.get("blocked_prs")) for row in destination_rows)
        + sum(_int(row.get("blocked_prs")) for row in rehab_rows),
        "alert_warnings": len(warnings),
    }


def render_dashboard(state: dict[str, Any]) -> str:
    rows = list(state.get("rows", []))
    activity = list(state.get("activity", []))
    destination_rows = list(state.get("destination_repos", []))
    rehab_rows = list(state.get("rehab_repos", []))
    warnings = list(state.get("warnings", []))
    summary = dict(state.get("summary", {}))
    ready = [row for row in rows if _is_ready_update(row)]
    triage = [row for row in rows if _is_triage_update(row)]
    blocked = [row for row in rows if _is_blocked_update(row)]
    lines = [
        "# Fleet Update Dashboard",
        "",
        f"Last updated: `{state.get('generated_at', '')}`",
        "",
        "> Source repo updates start in each `<app>-aio` repo. `awesome-unraid` is the downstream catalog destination and sync follows validated source changes.",
        "",
        "## Summary",
        "",
        "| Active | Destination | Rehab | Updates | Ready | Triage | Blocked | Runtime Deferred | Open PRs | Open Issues | Alert Warnings |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        "| {active_repos} | {destination_repos} | {rehab_repos} | {upstream_updates} | {ready_updates} | {triage_updates} | {blocked_updates} | {runtime_deferred} | {open_prs} | {open_issues} | {alert_warnings} |".format(
            **{key: _cell(summary.get(key, 0)) for key in _summary_keys()}
        ),
        "",
    ]
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    _render_safety_review_section(lines, rows)
    _render_update_section(lines, "Ready To Merge", ready)
    _render_update_section(lines, "Needs Triage", triage)
    _render_update_section(lines, "Blocked", blocked)
    _render_destination_section(lines, destination_rows)
    _render_rehab_section(lines, rehab_rows)
    _render_activity_section(lines, activity, destination_rows, rehab_rows)
    _render_fleet_state_section(lines, rows)
    _render_controls(lines)
    _render_next_commands(lines, rows, destination_rows, rehab_rows)
    lines.extend(
        [
            "",
            STATE_START_BASE64,
            _encoded_dashboard_state(state),
            STATE_END,
            "",
        ]
    )
    return "\n".join(lines)


def _encoded_dashboard_state(state: dict[str, Any]) -> str:
    raw = json.dumps(state, indent=2, sort_keys=True).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def alert_warnings(env: dict[str, str]) -> list[str]:
    warnings: list[str] = []
    if not env.get("AIO_FLEET_ALERT_WEBHOOK_URL"):
        warnings.append(
            "AIO_FLEET_ALERT_WEBHOOK_URL is not configured; rich digest alerts are disabled."
        )
    return warnings


def upsert_dashboard_issue(
    *,
    issue_repo: str,
    body: str,
    issue_number: int | None = None,
    title: str = DASHBOARD_TITLE,
    label: str = DASHBOARD_LABEL,
    dry_run: bool,
) -> DashboardIssueResult:
    existing = (
        _dashboard_issue_by_number(issue_repo, issue_number)
        if issue_number
        else _find_dashboard_issue(issue_repo, label=label)
    )
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
            ]
        )
        _add_dashboard_label(issue_repo, number=number, label=label)
        _close_duplicate_dashboard_issues(
            issue_repo,
            canonical_number=number,
            title=title,
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


def dashboard_commands_from_body(body: str) -> dict[str, bool]:
    checked = {
        match.group("label").strip() for match in CHECKED_COMMAND_RE.finditer(body)
    }
    return {name: label in checked for name, label in DASHBOARD_COMMANDS.items()}


def dashboard_issue_commands(*, issue_repo: str, issue_number: int) -> dict[str, Any]:
    issue = _gh_json(
        [
            "issue",
            "view",
            str(issue_number),
            "--repo",
            issue_repo,
            "--json",
            "number,title,state,body,labels,url",
        ]
    )
    if not isinstance(issue, dict):
        return {
            "issue_number": issue_number,
            "issue_url": "",
            "is_dashboard": False,
            "commands": {},
        }
    labels = [
        str(label.get("name", ""))
        for label in issue.get("labels", [])
        if isinstance(label, dict)
    ]
    is_dashboard = (
        str(issue.get("title", "")) == DASHBOARD_TITLE
        and str(issue.get("state", "")).upper() == "OPEN"
        and DASHBOARD_LABEL in labels
    )
    commands = (
        dashboard_commands_from_body(str(issue.get("body", ""))) if is_dashboard else {}
    )
    return {
        "issue_number": issue_number,
        "issue_url": str(issue.get("url", "")),
        "is_dashboard": is_dashboard,
        "commands": commands,
        "requested": any(commands.values()),
    }


def repo_activity(name: str, github_repo: str, stale_days: int) -> dict[str, Any]:
    try:
        prs = _gh_json(
            [
                "pr",
                "list",
                "--repo",
                github_repo,
                "--state",
                "open",
                "--json",
                "number,title,url,isDraft,mergeStateStatus,statusCheckRollup,createdAt,headRefOid",
            ]
        )
        issues = _gh_json(
            [
                "issue",
                "list",
                "--repo",
                github_repo,
                "--state",
                "open",
                "--json",
                "number,title,url,createdAt",
            ]
        )
    except Exception as exc:
        return {
            "repo": name,
            "github_repo": github_repo,
            "activity_state": "unknown",
            "error": str(exc),
            "open_prs": "unknown",
            "open_issues": "unknown",
            "draft_prs": "unknown",
            "blocked_prs": "unknown",
            "clean_prs": "unknown",
            "stale_prs": "unknown",
            "oldest_pr_age_days": "unknown",
            "newest_issue_age_days": "unknown",
            "prs": [],
        }
    prs = prs if isinstance(prs, list) else []
    issues = issues if isinstance(issues, list) else []
    pr_rows = [
        _pr_summary(pr, stale_days=stale_days) for pr in prs if isinstance(pr, dict)
    ]
    issue_ages = [
        _age_days(issue.get("createdAt")) for issue in issues if isinstance(issue, dict)
    ]
    pr_ages = [_age_days(pr.get("createdAt")) for pr in prs if isinstance(pr, dict)]
    return {
        "repo": name,
        "github_repo": github_repo,
        "activity_state": "ok",
        "open_prs": len(prs),
        "open_issues": len(issues),
        "draft_prs": len([pr for pr in pr_rows if pr["state"] == "draft"]),
        "blocked_prs": len([pr for pr in pr_rows if pr["state"] == "blocked"]),
        "clean_prs": len([pr for pr in pr_rows if pr["state"] == "ready"]),
        "stale_prs": len([pr for pr in pr_rows if pr["stale"]]),
        "oldest_pr_age_days": max(pr_ages) if pr_ages else 0,
        "newest_issue_age_days": min(issue_ages) if issue_ages else 0,
        "prs": pr_rows,
    }


def _template_row(repo: RepoConfig, *, include_registry: bool) -> dict[str, Any]:
    return {
        "repo": repo.name,
        "component": "template",
        "current": "",
        "latest": "",
        "strategy": "manual",
        "update": False,
        "pr": "",
        "check": "not-applicable",
        "signed": "not-applicable",
        "registry": "manual" if include_registry else "not-run",
        "release": "manual",
        "safety": "not-applicable",
        "safety_confidence": "",
        "config_delta": "",
        "template_impact": "",
        "runtime_smoke": "",
        "safety_signals": [],
        "safety_warnings": [],
        "safety_failures": [],
        "next_action": "manual template baseline",
    }


def _monitor_failure_row(repo: RepoConfig, exc: Exception) -> dict[str, Any]:
    return {
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
        "safety": "unknown",
        "safety_confidence": "",
        "config_delta": "unknown",
        "template_impact": "unknown",
        "runtime_smoke": "unknown",
        "safety_signals": [],
        "safety_warnings": [str(exc)],
        "safety_failures": [],
        "next_action": f"upstream monitor failed: {exc}",
    }


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
    safety = _safety_assessment(repo, result, pr, signed=signed, check=check)
    registry = "not-run"
    if include_registry:
        registry = "current-not-verified"
    release = "after-merge" if result.updates_available else "current"
    next_action = _next_action(
        result,
        pr=pr,
        signed=signed,
        check=check,
        safety=safety,
    )
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
        "safety": safety["safety_level"],
        "safety_confidence": safety["confidence"],
        "config_delta": safety["config_delta"],
        "template_impact": safety["template_impact"],
        "runtime_smoke": safety["runtime_smoke"],
        "safety_signals": safety.get("signals", []),
        "safety_warnings": safety["warnings"],
        "safety_failures": safety["failures"],
        "next_action": next_action,
    }


def _destination_row(
    manifest: FleetManifest,
    ref: DashboardRepoRef,
    *,
    active_rows: list[dict[str, Any]],
    include_activity: bool,
    stale_days: int,
) -> dict[str, Any]:
    activity = (
        repo_activity(ref.name, ref.github_repo, stale_days)
        if include_activity
        else _empty_activity(ref.name, ref.github_repo)
    )
    catalog_path = Path(str(ref.raw.get("catalog_path") or ref.path))
    failures = catalog_repo_failures(manifest, catalog_path)
    sync_queue = [
        {
            "repo": row.get("repo", ""),
            "component": row.get("component", ""),
            "pr": row.get("pr", ""),
            "state": row.get("next_action", ""),
        }
        for row in active_rows
        if _is_ready_update(row)
    ]
    return {
        **activity,
        "kind": "destination",
        "role": str(ref.raw.get("role", "destination")),
        "description": str(ref.raw.get("description", "")),
        "catalog_state": "ok" if not failures else f"{len(failures)} finding(s)",
        "catalog_findings": failures[:10],
        "sync_queue": sync_queue,
        "sync_queue_count": len(sync_queue),
        "next_action": str(ref.raw.get("next_action", "validate destination repo")),
    }


def _rehab_row(
    ref: DashboardRepoRef,
    *,
    include_activity: bool,
    stale_days: int,
) -> dict[str, Any]:
    activity = (
        repo_activity(ref.name, ref.github_repo, stale_days)
        if include_activity
        else _empty_activity(ref.name, ref.github_repo)
    )
    repo_config = RepoConfig(
        name=ref.name,
        raw={
            "path": str(ref.path),
            "app_slug": ref.name,
            "image_name": f"jsonbored/{ref.name}",
            "docker_cache_scope": f"{ref.name}-image",
            "pytest_image_tag": f"{ref.name}:pytest",
        },
        defaults={},
        owner=ref.github_repo.split("/", 1)[0],
    )
    findings = cleanup_findings(repo_config) if ref.path.exists() else []
    git_state = _git_state(ref.path)
    return {
        **activity,
        "kind": "rehab",
        "status": str(ref.raw.get("status", "rehab")),
        "description": str(ref.raw.get("description", "")),
        "branch": git_state["branch"],
        "dirty": git_state["dirty"],
        "path_exists": git_state["path_exists"],
        "cleanup_findings": len(findings),
        "cleanup_paths": [finding.path.name for finding in findings[:10]],
        "next_action": str(ref.raw.get("next_action", "run rehab onboarding")),
        "checklist": rehab_checklist(ref.name),
    }


def rehab_checklist(repo: str) -> list[str]:
    return [
        "sync local repo to main",
        "inspect Dockerfile, runtime wrapper, XML, README, and support docs",
        "decide publish profile and upstream monitor strategy",
        "export .aio-fleet.yml from fleet.yml once manifest entry is ready",
        "remove legacy workflows/config/scripts that aio-fleet replaces",
        "run central validation and cleanup verification",
        "prove aio-fleet / required on a real PR",
        f"promote {repo} to active fleet only after validation passes",
    ]


def _next_action(
    result: UpstreamMonitorResult,
    *,
    pr: dict[str, Any] | None,
    signed: str,
    check: str,
    safety: dict[str, Any],
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
    if safety.get("safety_level") == "blocked":
        return str(safety.get("next_action", "resolve safety failure before merge"))
    if safety.get("safety_level") == "warn":
        return str(safety.get("next_action", "review safety warnings before merge"))
    return "human review and merge"


def _safety_assessment(
    repo: RepoConfig,
    result: UpstreamMonitorResult,
    pr: dict[str, Any] | None,
    *,
    signed: str,
    check: str,
) -> dict[str, Any]:
    if not result.updates_available:
        return {
            "safety_level": "not-needed",
            "confidence": "",
            "config_delta": "none",
            "template_impact": "none",
            "runtime_smoke": "not-run",
            "signals": [],
            "warnings": [],
            "failures": [],
            "next_action": "none",
        }
    try:
        return assess_upstream_pr(
            repo,
            result=result,
            pr=pr,
            signed_state=signed,
            check_state=check,
        ).to_dict()
    except Exception as exc:
        return {
            "safety_level": "warn",
            "confidence": 0.25,
            "config_delta": "unknown",
            "template_impact": "unknown",
            "runtime_smoke": "unknown",
            "signals": [],
            "warnings": [f"safety assessment failed: {exc}"],
            "failures": [],
            "next_action": "review safety assessment failure before merge",
        }


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
            "number,url,files,headRefName,baseRefName,headRefOid,mergeStateStatus,statusCheckRollup",
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
    candidates = _dashboard_issue_candidates(issue_repo, label=label)
    labeled = [issue for issue in candidates if _issue_has_label(issue, label)]
    if labeled:
        return _newest_issue(labeled)
    hidden_state = [
        issue for issue in candidates if STATE_START in str(issue.get("body", ""))
    ]
    if hidden_state:
        return _newest_issue(hidden_state)
    exact_title = [
        issue for issue in candidates if str(issue.get("title", "")) == DASHBOARD_TITLE
    ]
    return _newest_issue(exact_title)


def _dashboard_issue_by_number(
    issue_repo: str, issue_number: int | None
) -> dict[str, Any] | None:
    if not issue_number:
        return None
    result = _run(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--repo",
            issue_repo,
            "--json",
            "number,title,url,labels,updatedAt,body,state",
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        issue = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(issue, dict):
        return None
    if str(issue.get("state", "")).upper() != "OPEN":
        return None
    return issue


def _dashboard_issue_candidates(issue_repo: str, *, label: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
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
            "number,title,url,labels,updatedAt,body",
        ],
        check=False,
    )
    if result.returncode == 0:
        issues.extend(_json_issue_list(result.stdout))
    title_result = _run(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            issue_repo,
            "--state",
            "open",
            "--search",
            f'"{DASHBOARD_TITLE}" in:title',
            "--json",
            "number,title,url,labels,updatedAt,body",
        ],
        check=False,
    )
    if title_result.returncode == 0:
        issues.extend(_json_issue_list(title_result.stdout))
    unique: dict[int, dict[str, Any]] = {}
    for issue in issues:
        try:
            unique[int(issue.get("number"))] = issue
        except (TypeError, ValueError):
            continue
    return list(unique.values())


def _json_issue_list(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    return (
        [issue for issue in data if isinstance(issue, dict)]
        if isinstance(data, list)
        else []
    )


def _newest_issue(issues: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not issues:
        return None
    return sorted(
        issues,
        key=lambda issue: str(issue.get("updatedAt", "")),
        reverse=True,
    )[0]


def _issue_has_label(issue: dict[str, Any], label: str) -> bool:
    return any(
        isinstance(item, dict) and item.get("name") == label
        for item in issue.get("labels", [])
    )


def _close_duplicate_dashboard_issues(
    issue_repo: str, *, canonical_number: int, title: str
) -> None:
    for issue in _dashboard_issue_candidates(issue_repo, label=DASHBOARD_LABEL):
        try:
            number = int(issue.get("number"))
        except (TypeError, ValueError):
            continue
        if number == canonical_number or str(issue.get("title", "")) != title:
            continue
        _run(
            [
                "gh",
                "issue",
                "close",
                str(number),
                "--repo",
                issue_repo,
                "--reason",
                "not planned",
            ],
            check=False,
        )


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


def _add_dashboard_label(issue_repo: str, *, number: int, label: str) -> None:
    _run(
        [
            "gh",
            "issue",
            "edit",
            str(number),
            "--repo",
            issue_repo,
            "--add-label",
            label,
        ],
        check=False,
    )


def _dashboard_repo_refs(manifest: FleetManifest, group: str) -> list[DashboardRepoRef]:
    dashboard = manifest.raw.get("dashboard", {})
    if not isinstance(dashboard, dict):
        return []
    raw_group = dashboard.get(group, {})
    if not isinstance(raw_group, dict):
        return []
    refs: list[DashboardRepoRef] = []
    for name, config in raw_group.items():
        if not isinstance(config, dict):
            continue
        refs.append(
            DashboardRepoRef(
                name=str(name),
                github_repo=str(config.get("github_repo", f"{manifest.owner}/{name}")),
                path=Path(str(config.get("path", f"../{name}"))),
                raw=dict(config),
            )
        )
    return refs


def _pr_summary(pr: dict[str, Any], *, stale_days: int) -> dict[str, Any]:
    merge_state = str(pr.get("mergeStateStatus") or "UNKNOWN")
    check = _check_state(pr)
    age = _age_days(pr.get("createdAt"))
    state = "ready"
    if pr.get("isDraft"):
        state = "draft"
    elif merge_state in {"BLOCKED", "DIRTY", "BEHIND", "UNKNOWN"} or check in {
        "failure",
        "timed_out",
        "cancelled",
    }:
        state = "blocked"
    elif merge_state != "CLEAN":
        state = "attention"
    return {
        "number": pr.get("number", ""),
        "title": pr.get("title", ""),
        "url": pr.get("url", ""),
        "draft": bool(pr.get("isDraft")),
        "merge_state": merge_state,
        "check": check,
        "age_days": age,
        "stale": age >= stale_days,
        "state": state,
    }


def _age_days(value: object) -> int:
    if not value:
        return 0
    try:
        created = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return 0
    now = datetime.now(UTC)
    return max(0, (now - created).days)


def _git_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path_exists": False, "branch": "missing", "dirty": "unknown"}
    branch = _run(["git", "branch", "--show-current"], check=False, cwd=path)
    status = _run(["git", "status", "--short"], check=False, cwd=path)
    return {
        "path_exists": True,
        "branch": branch.stdout.strip() if branch.returncode == 0 else "unknown",
        "dirty": bool(status.stdout.strip()) if status.returncode == 0 else "unknown",
    }


def _gh_json(args: list[str]) -> Any:
    result = _run(["gh", *args], check=True)
    text = result.stdout.strip()
    return json.loads(text) if text else None


def _empty_activity(name: str, github_repo: str) -> dict[str, Any]:
    return {
        "repo": name,
        "github_repo": github_repo,
        "activity_state": "skipped",
        "open_prs": "not-run",
        "open_issues": "not-run",
        "draft_prs": "not-run",
        "blocked_prs": "not-run",
        "clean_prs": "not-run",
        "stale_prs": "not-run",
        "oldest_pr_age_days": "not-run",
        "newest_issue_age_days": "not-run",
        "prs": [],
    }


def _render_update_section(
    lines: list[str], title: str, rows: list[dict[str, Any]]
) -> None:
    lines.extend([f"## {title}", ""])
    if not rows:
        lines.extend(["- none", ""])
        return
    lines.extend(
        [
            "| Repo | Component | Current | Latest | PR | Check | Signed | Safety | Config Delta | Template Impact | Runtime Smoke | Next |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            "| {repo} | {component} | {current} | {latest} | {pr} | {check} | {signed} | {safety} | {config_delta} | {template_impact} | {runtime_smoke} | {next_action} |".format(
                **{key: _cell(row.get(key, "")) for key in row}
            )
        )
    lines.append("")


def _render_safety_review_section(lines: list[str], rows: list[dict[str, Any]]) -> None:
    update_rows = [row for row in rows if row.get("update")]
    lines.extend(["## Safety Review", ""])
    lines.extend(
        [
            "`ok` means no clear fleet-policy blocker was found. `warn` means human review is still required. `manual` means notify-only or not enough source data. `blocked` means fix before merge.",
            "",
        ]
    )
    if not update_rows:
        lines.extend(["- no upstream updates to review", ""])
        return
    lines.extend(
        [
            "| Repo | Component | Safety | Config | Runtime | Evidence | Next |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in update_rows:
        lines.append(
            "| {repo} | {component} | {safety} | {config_delta} | {runtime_smoke} | {evidence} | {next_action} |".format(
                repo=_cell(row.get("repo", "")),
                component=_cell(row.get("component", "")),
                safety=_cell(row.get("safety", "")),
                config_delta=_cell(row.get("config_delta", "")),
                runtime_smoke=_cell(row.get("runtime_smoke", "")),
                evidence=_cell(_safety_evidence(row)),
                next_action=_cell(row.get("next_action", "")),
            )
        )
    lines.append("")


def _render_destination_section(
    lines: list[str], destination_rows: list[dict[str, Any]]
) -> None:
    lines.extend(["## Destination Repo", ""])
    if not destination_rows:
        lines.extend(["- none configured", ""])
        return
    lines.extend(
        [
            "| Repo | Role | Catalog | Sync Queue | PRs | Issues | Next |",
            "| --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in destination_rows:
        lines.append(
            "| {repo} | {role} | {catalog_state} | {sync_queue_count} | {open_prs} | {open_issues} | {next_action} |".format(
                **{key: _cell(row.get(key, "")) for key in row}
            )
        )
    lines.extend(
        [
            "",
            "> Direct catalog edits should stay limited to catalog-only metadata or assets. App template changes start in the source repo.",
            "",
        ]
    )


def _render_rehab_section(lines: list[str], rehab_rows: list[dict[str, Any]]) -> None:
    lines.extend(["## Rehab / Onboarding", ""])
    if not rehab_rows:
        lines.extend(["- none configured", ""])
        return
    lines.extend(
        [
            "| Repo | Status | Branch | Dirty | Cleanup Findings | PRs | Issues | Next |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in rehab_rows:
        lines.append(
            "| {repo} | {status} | {branch} | {dirty} | {cleanup_findings} | {open_prs} | {open_issues} | {next_action} |".format(
                **{key: _cell(row.get(key, "")) for key in row}
            )
        )
    lines.append("")
    for row in rehab_rows:
        lines.append(f"**{row['repo']} first rehab checklist**")
        for item in row.get("checklist", []):
            lines.append(f"- [ ] {item}")
        lines.append("")


def _render_activity_section(
    lines: list[str],
    activity_rows: list[dict[str, Any]],
    destination_rows: list[dict[str, Any]],
    rehab_rows: list[dict[str, Any]],
) -> None:
    rows = [*activity_rows, *destination_rows, *rehab_rows]
    lines.extend(["## Fleet Activity", ""])
    if not rows:
        lines.extend(["- no activity collected", ""])
        return
    lines.extend(
        [
            "| Repo | PRs | Ready | Blocked | Draft | Stale | Issues | Oldest PR | Newest Issue |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in rows:
        lines.append(
            "| {repo} | {open_prs} | {clean_prs} | {blocked_prs} | {draft_prs} | {stale_prs} | {open_issues} | {oldest_pr_age} | {newest_issue_age} |".format(
                repo=_cell(row.get("repo", "")),
                open_prs=_cell(row.get("open_prs", "")),
                clean_prs=_cell(row.get("clean_prs", "")),
                blocked_prs=_cell(row.get("blocked_prs", "")),
                draft_prs=_cell(row.get("draft_prs", "")),
                stale_prs=_cell(row.get("stale_prs", "")),
                open_issues=_cell(row.get("open_issues", "")),
                oldest_pr_age=_age_cell(row.get("oldest_pr_age_days")),
                newest_issue_age=_age_cell(row.get("newest_issue_age_days")),
            )
        )
    lines.append("")


def _render_fleet_state_section(lines: list[str], rows: list[dict[str, Any]]) -> None:
    lines.extend(
        [
            "## Update Queue",
            "",
            "| Repo | Component | Current | Latest | Strategy | PR | Check | Signed | Safety | Config Delta | Template Impact | Runtime Smoke | Registry | Release | Next |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            "| {repo} | {component} | {current} | {latest} | {strategy} | {pr} | {check} | {signed} | {safety} | {config_delta} | {template_impact} | {runtime_smoke} | {registry} | {release} | {next_action} |".format(
                **{key: _cell(row.get(key, "")) for key in row}
            )
        )
    lines.append("")


def _render_controls(lines: list[str]) -> None:
    lines.extend(
        [
            "## Controls",
            "",
            "- [ ] Rescan dashboard",
            "- [ ] Run upstream monitor",
            "",
            "> Check one box to trigger the central workflow. The dashboard rewrites this issue body in place and resets controls after the run.",
            "",
        ]
    )


def _render_next_commands(
    lines: list[str],
    rows: list[dict[str, Any]],
    destination_rows: list[dict[str, Any]],
    rehab_rows: list[dict[str, Any]],
) -> None:
    lines.extend(["## Next Commands", ""])
    commands: list[str] = []
    for row in rows:
        if row.get("update") and row.get("strategy") == "notify":
            commands.append(
                f"python -m aio_fleet upstream assess --repo {row['repo']} --format json"
            )
        elif row.get("update") and row.get("safety") in {"warn", "blocked"}:
            pr_url = _markdown_url(row.get("pr"))
            pr_number = pr_url.rstrip("/").rsplit("/", 1)[-1] if pr_url else ""
            if pr_number.isdigit():
                commands.append(
                    f"python -m aio_fleet upstream assess --repo {row['repo']} --pr {pr_number} --format json"
                )
            else:
                commands.append(
                    f"python -m aio_fleet upstream assess --repo {row['repo']} --format json"
                )
        elif _is_ready_update(row):
            pr_url = _markdown_url(row.get("pr"))
            if pr_url:
                commands.append(f"gh pr view {pr_url}")
    if destination_rows:
        commands.append(
            "python -m aio_fleet validate-catalog --catalog-path ../awesome-unraid"
        )
    for row in rehab_rows:
        commands.append(
            f"python -m aio_fleet onboard-repo --repo {row['repo']} --mode rehab"
        )
    if not commands:
        lines.extend(["- no immediate commands", ""])
        return
    lines.append("```bash")
    lines.extend(dict.fromkeys(commands))
    lines.append("```")
    lines.append("")


def _is_ready_update(row: dict[str, Any]) -> bool:
    return (
        bool(row.get("update"))
        and row.get("next_action") == "human review and merge"
        and row.get("check") == "success"
        and row.get("signed") == "verified"
        and row.get("safety") in {"ok", "not-needed", ""}
    )


def _is_triage_update(row: dict[str, Any]) -> bool:
    return (
        bool(row.get("update"))
        and not _is_blocked_update(row)
        and (row.get("strategy") == "notify" or row.get("safety") in {"warn", "manual"})
    )


def _is_blocked_update(row: dict[str, Any]) -> bool:
    if not row.get("update") or row.get("strategy") == "notify":
        return False
    if row.get("safety") == "blocked":
        return True
    if row.get("safety") in {"warn", "manual"}:
        return False
    action = str(row.get("next_action", ""))
    return action != "human review and merge"


def _safety_evidence(row: dict[str, Any]) -> str:
    for key in ("safety_failures", "safety_warnings", "safety_signals"):
        values = row.get(key, [])
        if isinstance(values, list) and values:
            return str(values[0])
    return "no clear blocker found"


def _summary_keys() -> list[str]:
    return [
        "active_repos",
        "destination_repos",
        "rehab_repos",
        "upstream_updates",
        "ready_updates",
        "triage_updates",
        "blocked_updates",
        "runtime_deferred",
        "open_prs",
        "open_issues",
        "alert_warnings",
    ]


def _issue_number_from_url(url: str) -> int | None:
    try:
        return int(url.rstrip("/").rsplit("/", 1)[1])
    except (IndexError, ValueError):
        return None


def _run(
    command: list[str], *, check: bool = True, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = _github_cli_env() if command and command[0] == "gh" else None
    result = subprocess.run(  # nosec B603
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{' '.join(command)} failed: {detail}")
    return result


def _github_cli_env() -> dict[str, str] | None:
    token = _github_cli_token()
    if not token:
        return None
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env.pop("GITHUB_TOKEN", None)
    return env


def _github_cli_token() -> str:
    for key in (
        "AIO_FLEET_DASHBOARD_TOKEN",
        "AIO_FLEET_UPSTREAM_TOKEN",
        "AIO_FLEET_CHECK_TOKEN",
        "APP_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    ):
        token = os.environ.get(key, "").strip()
        if token:
            return token
    return ""


def _cell(value: object) -> str:
    text = str(value).replace("\n", " ").replace("|", "\\|").strip()
    return text or "-"


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _age_cell(value: object) -> str:
    return f"{value}d" if isinstance(value, int) else _cell(value)


def _markdown_url(value: object) -> str:
    text = str(value or "")
    if "](" not in text:
        return text if text.startswith("http") else ""
    return text.split("](", 1)[1].rstrip(")")
