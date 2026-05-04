from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shlex
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path

from aio_fleet.alerts import alert_payload, emit_alert, payload_from_report
from aio_fleet.app_manifest import (
    APP_MANIFEST_NAME,
    app_manifest_from_repo,
    load_app_manifest,
    render_app_manifest,
)
from aio_fleet.catalog import sync_catalog_assets, unpublished_xml_targets
from aio_fleet.changelog import (
    build_release_plan,
    update_template_changes,
    write_temp_git_cliff_config,
)
from aio_fleet.checks import (
    CHECK_NAME,
    check_run_payload,
    check_run_satisfied,
    upsert_check_run,
)
from aio_fleet.cleanup import cleanup_findings, remove_cleanup_findings
from aio_fleet.control_plane import (
    central_check_steps,
    publish_components,
    registry_publish_command,
    run_central_trunk,
    run_steps,
)
from aio_fleet.fleet_dashboard import dashboard_report, upsert_dashboard_issue
from aio_fleet.github_policy import load_policy, validate_github_policy
from aio_fleet.manifest import FleetManifest, ManifestError, RepoConfig, load_manifest
from aio_fleet.poll import poll_targets
from aio_fleet.registry import compute_registry_tags, verify_registry_tags
from aio_fleet.release import find_release_target_commit, latest_changelog_version
from aio_fleet.upstream import (
    create_or_update_upstream_pr,
    monitor_repo,
    result_dict,
)
from aio_fleet.validators import (
    TRACKED_ARTIFACT_PATTERNS,
    catalog_asset_failures,
    catalog_quality_findings,
    catalog_repo_failures,
    derived_repo_failures,
    pinned_action_failures,
    repo_policy_failures,
    template_metadata_failures,
    tracked_artifact_failures,
)


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        cmd, cwd=cwd, check=False, text=True, capture_output=True
    )


def _repo_python(repo_path: Path) -> str:
    for candidate in (
        repo_path / ".venv" / "bin" / "python",
        repo_path / ".venv" / "bin" / "python3",
    ):
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return sys.executable


def _current_ref() -> str:
    result = _run(["git", "rev-parse", "HEAD"])
    if result.returncode != 0:
        return "main"
    return result.stdout.strip()


def _git_head(repo_path: Path) -> str:
    result = _run(["git", "rev-parse", "HEAD"], cwd=repo_path)
    if result.returncode != 0:
        raise ManifestError(f"unable to resolve HEAD in {repo_path}")
    return result.stdout.strip()


def _repo_for_identifier(manifest: FleetManifest, identifier: str) -> RepoConfig:
    if identifier in manifest.repos:
        return manifest.repo(identifier)
    matches = [repo for repo in manifest.repos.values() if repo.app_slug == identifier]
    if len(matches) == 1:
        return matches[0]
    raise ManifestError(f"unknown repo or app slug in fleet.yml: {identifier}")


def _repo_with_path(repo: RepoConfig, path: Path) -> RepoConfig:
    raw = dict(repo.raw)
    raw["path"] = str(path)
    return RepoConfig(name=repo.name, raw=raw, defaults=repo.defaults, owner=repo.owner)


def _app_manifest_failures(repo: RepoConfig) -> list[str]:
    path = repo.path / APP_MANIFEST_NAME
    if not path.exists():
        return [f"{repo.name}: missing {APP_MANIFEST_NAME}"]
    failures: list[str] = []
    try:
        actual = load_app_manifest(path)
    except ManifestError as exc:
        failures.append(f"{repo.name}: {APP_MANIFEST_NAME} invalid: {exc}")
        return failures

    expected = app_manifest_from_repo(repo)
    if actual != expected:
        failures.append(
            f"{repo.name}: {APP_MANIFEST_NAME} drifted from fleet.yml; run aio-fleet export-app-manifest --repo {repo.name} --write"
        )
    return failures


def cmd_doctor(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    failures: list[str] = []
    for name, repo in manifest.repos.items():
        if not repo.path.exists():
            failures.append(f"{name}: repo path missing: {repo.path}")
            continue
        for required in [
            "Dockerfile",
            "README.md",
            "pyproject.toml",
            APP_MANIFEST_NAME,
        ]:
            if not (repo.path / required).exists():
                failures.append(f"{name}: missing {required}")
        failures.extend(_app_manifest_failures(repo))
        failures.extend(catalog_asset_failures(repo))
        failures.extend(tracked_artifact_failures(repo.path))
    if args.github:
        failures.extend(
            validate_github_policy(
                Path(args.policy),
                check_secrets=args.check_secrets,
            )
        )
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"fleet manifest ok: {len(manifest.repos)} repos")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    policy_repos: set[str] = set()
    catalog_path = Path(args.catalog_path).resolve() if args.catalog_path else None
    if args.github:
        try:
            policy_repos = set(load_policy(Path(args.policy))["repositories"])
        except Exception:
            policy_repos = set()
    for name, repo in manifest.repos.items():
        branch = _run(["git", "branch", "--show-current"], cwd=repo.path)
        status = _run(["git", "status", "--short"], cwd=repo.path)
        dirty = "dirty" if status.stdout.strip() else "clean"
        drift = _run(
            ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"],
            cwd=repo.path,
        )
        drift_state = ""
        ahead = "?"
        behind = "?"
        if drift.returncode == 0:
            ahead, behind = (drift.stdout.strip().split() + ["0", "0"])[:2]
            drift_state = f" ahead={ahead} behind={behind}"
        current_pr_state = ""
        open_pr_state = ""
        policy_state = ""
        branch_name = branch.stdout.strip()
        if args.github and branch_name:
            current_pr = _run(
                [
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    repo.github_repo,
                    "--head",
                    branch_name,
                    "--json",
                    "number,url,isDraft,statusCheckRollup",
                    "--jq",
                    (
                        '.[0] // null | if . == null then "no-pr" '
                        'else "#\\(.number) \\(.url) draft=\\(.isDraft) checks=\\(.statusCheckRollup | length)" end'
                    ),
                ],
                cwd=repo.path,
            )
            current_pr_state = (
                f" current_pr={current_pr.stdout.strip() or 'pr-unknown'}"
            )
            open_prs = _run(
                [
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    repo.github_repo,
                    "--state",
                    "open",
                    "--json",
                    "number",
                    "--jq",
                    "length",
                ],
                cwd=repo.path,
            )
            open_pr_state = f" open_prs={open_prs.stdout.strip() or 'unknown'}"
            if name not in policy_repos:
                policy_state = " policy=manual"
            else:
                try:
                    failures = validate_github_policy(
                        Path(args.policy), repos=[name], check_secrets=False
                    )
                    policy_state = (
                        " policy=ok"
                        if not failures
                        else f" policy={len(failures)}-drift"
                    )
                except Exception as exc:
                    policy_state = f" policy=unknown:{exc}"
        catalog_state = f" {_catalog_status(repo, catalog_path)}"
        publish_state = f" {_publish_status(repo, dirty=dirty, behind=behind, policy_state=policy_state)}"
        print(
            f"{name:22} {branch_name or '-':36} "
            f"{dirty}{drift_state}{current_pr_state}{open_pr_state}{policy_state}{catalog_state}{publish_state}"
        )
    return 0


def _catalog_status(repo: RepoConfig, catalog_path: Path | None) -> str:
    if repo.raw.get("catalog_published") is False:
        return "catalog=held"
    if catalog_path is None:
        return "catalog=published"

    missing = [
        target
        for target in _catalog_asset_targets(repo)
        if target and not (catalog_path / target).exists()
    ]
    if missing:
        return f"catalog=missing:{','.join(missing)}"
    return "catalog=ok"


def _catalog_asset_targets(repo: RepoConfig) -> list[str]:
    assets = repo.raw.get("catalog_assets", [])
    if not isinstance(assets, list):
        return []
    return [
        str(asset.get("target", "")).strip()
        for asset in assets
        if isinstance(asset, dict) and str(asset.get("target", "")).strip()
    ]


def _publish_status(
    repo: RepoConfig, *, dirty: str, behind: str, policy_state: str
) -> str:
    if repo.publish_profile == "template":
        return "publish=manual"
    if dirty != "clean":
        return "publish=blocked:dirty"
    if behind not in {"0", "?"}:
        return "publish=blocked:behind"
    if policy_state.startswith(" policy=") and policy_state != " policy=ok":
        return "publish=blocked:policy"
    return "publish=source-ready"


def cmd_debt_report(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    catalog_path = Path(args.catalog_path).resolve() if args.catalog_path else None
    catalog_failures = (
        catalog_repo_failures(manifest, catalog_path) if catalog_path else []
    )
    report: dict[str, object] = {
        "ref": "control-plane",
        "catalog_path": str(catalog_path) if catalog_path else None,
        "repos": [],
    }
    policy_repos: set[str] = set()
    if args.github:
        policy_repos = set(load_policy(Path(args.policy))["repositories"])

    for repo in manifest.repos.values():
        git_status = _run(["git", "status", "--short"], cwd=repo.path)
        dirty = "dirty" if git_status.stdout.strip() else "clean"
        drift = _run(
            ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"],
            cwd=repo.path,
        )
        ahead = behind = "?"
        if drift.returncode == 0:
            ahead, behind = (drift.stdout.strip().split() + ["0", "0"])[:2]

        retired_shared_paths = [
            str(finding.path.relative_to(repo.path))
            for finding in cleanup_findings(repo)
        ]
        repo_catalog_failures = [
            failure
            for failure in catalog_failures
            if failure.startswith(f"{repo.name}:")
        ]
        github_policy_failures: list[str] = []
        if args.github and repo.name in policy_repos:
            try:
                github_policy_failures = validate_github_policy(
                    Path(args.policy), repos=[repo.name], check_secrets=False
                )
            except Exception as exc:
                github_policy_failures = [f"{repo.name}: github policy unknown: {exc}"]
        trunk_state = _trunk_state(repo.path) if args.trunk else "not-run"
        open_prs = _open_prs(repo) if args.github else "not-run"
        repo_report = {
            "repo": repo.name,
            "path": str(repo.path),
            "dirty": dirty,
            "ahead": ahead,
            "behind": behind,
            "publish": _publish_status(
                repo,
                dirty=dirty,
                behind=behind,
                policy_state=(
                    " policy=ok" if not github_policy_failures else " policy=drift"
                ),
            ),
            "retired_shared_paths": retired_shared_paths,
            "catalog_failures": repo_catalog_failures,
            "pinned_action_failures": pinned_action_failures(repo.path),
            "tracked_artifacts": tracked_artifact_failures(repo.path),
            "untracked_artifacts": _untracked_artifacts(repo.path),
            "github_policy_failures": github_policy_failures,
            "open_prs": open_prs,
            "trunk": trunk_state,
            "dify_launch": _dify_launch_state(repo, repo_catalog_failures),
        }
        report["repos"].append(repo_report)  # type: ignore[index]

    repos = report["repos"]  # type: ignore[assignment]
    report["summary"] = {
        "repos": len(repos),
        "retired_shared_paths": sum(
            bool(item["retired_shared_paths"]) for item in repos
        ),
        "catalog_failures": len(catalog_failures),
        "tracked_artifacts": sum(bool(item["tracked_artifacts"]) for item in repos),
        "untracked_artifacts": sum(bool(item["untracked_artifacts"]) for item in repos),
    }

    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.format == "markdown":
        print(_debt_report_markdown(report))
    else:
        print(_debt_report_text(report))
    return 0


def _untracked_artifacts(repo_path: Path) -> list[str]:
    result = _run(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo_path
    )
    if result.returncode != 0:
        return [f"unable to inspect untracked files: {result.stderr.strip()}"]
    artifacts: list[str] = []
    for line in result.stdout.splitlines():
        if not line.startswith("?? "):
            continue
        path = line[3:].strip()
        if any(fnmatch.fnmatch(path, pattern) for pattern in TRACKED_ARTIFACT_PATTERNS):
            artifacts.append(path)
    return artifacts


def _trunk_state(repo_path: Path) -> str:
    if not (repo_path / ".trunk" / "trunk.yaml").exists():
        return "skipped:no-config"
    result = _run(
        [
            "trunk",
            "check",
            "--show-existing",
            "--all",
            "--no-fix",
            "--no-progress",
            "--color=false",
        ],
        cwd=repo_path,
    )
    if result.returncode == 0:
        return "ok"
    issue_line = next(
        (
            line.strip()
            for line in result.stdout.splitlines()
            if "issues" in line.lower()
        ),
        "",
    )
    return f"failed:{issue_line or result.returncode}"


def _open_prs(repo: RepoConfig) -> str:
    result = _run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo.github_repo,
            "--state",
            "open",
            "--json",
            "number",
            "--jq",
            "length",
        ],
        cwd=repo.path,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _dify_launch_state(repo: RepoConfig, catalog_failures: list[str]) -> str:
    if repo.name != "dify-aio":
        return "not-applicable"
    if repo.raw.get("catalog_published") is not True:
        return "held"
    if catalog_failures:
        return "blocked:catalog"
    return "catalog-ready"


def _debt_report_text(report: dict[str, object]) -> str:
    lines = ["AIO fleet debt report"]
    for item in report["repos"]:  # type: ignore[index]
        problems = []
        for key in [
            "retired_shared_paths",
            "catalog_failures",
            "pinned_action_failures",
            "tracked_artifacts",
            "untracked_artifacts",
            "github_policy_failures",
        ]:
            if item[key]:  # type: ignore[index]
                problems.append(key)
        status = "ok" if not problems else ",".join(problems)
        lines.append(
            f"{item['repo']}: {status} {item['publish']} trunk={item['trunk']} open_prs={item['open_prs']}"  # type: ignore[index]
        )
    lines.append(f"summary: {report['summary']}")
    return "\n".join(lines)


def _debt_report_markdown(report: dict[str, object]) -> str:
    lines = [
        "# AIO Fleet Debt Report",
        "",
        "| Repo | Status | Publish | Trunk | Open PRs |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report["repos"]:  # type: ignore[index]
        problems = []
        for key in [
            "retired_shared_paths",
            "catalog_failures",
            "pinned_action_failures",
            "tracked_artifacts",
            "untracked_artifacts",
            "github_policy_failures",
        ]:
            if item[key]:  # type: ignore[index]
                problems.append(key.replace("_", " "))
        status = "ok" if not problems else ", ".join(problems)
        lines.append(
            f"| {item['repo']} | {status} | {item['publish']} | {item['trunk']} | {item['open_prs']} |"  # type: ignore[index]
        )
    return "\n".join(lines)


def cmd_validate_actions(args: argparse.Namespace) -> int:
    failures = pinned_action_failures(Path(args.repo_path).resolve())
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("All workflow actions are pinned to full commit SHAs.")
    return 0


def cmd_validate_derived(args: argparse.Namespace) -> int:
    failures = derived_repo_failures(
        Path(args.repo_path).resolve(),
        strict_placeholders=args.strict_placeholders,
        template_xml=args.template_xml or os.environ.get("TEMPLATE_XML"),
    )
    if failures:
        print(
            "\n".join(f"template validation error: {failure}" for failure in failures),
            file=sys.stderr,
        )
        return 1
    print("Derived repo validation passed.")
    return 0


def cmd_validate_repo(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repo = _repo_for_identifier(manifest, args.repo)
    repo_at_path = _repo_with_path(repo, Path(args.repo_path).resolve())
    failures = repo_policy_failures(repo_at_path, manifest)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"{repo.name} fleet policy checks passed")
    return 0


def cmd_validate_template_common(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    if not args.all and not args.repo:
        print("--repo is required unless --all is used", file=sys.stderr)
        return 1
    if args.all and args.repo_path:
        print("--repo-path can only be used with --repo", file=sys.stderr)
        return 1
    repos = (
        list(manifest.repos.values())
        if args.all
        else [_repo_for_identifier(manifest, args.repo)]
    )
    failures: list[str] = []
    for repo in repos:
        repo_to_check = (
            _repo_with_path(repo, Path(args.repo_path).resolve())
            if args.repo_path
            else repo
        )
        failures.extend(template_metadata_failures(repo_to_check, manifest))
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    checked = len(repos)
    print(f"common template validation passed for {checked} repo(s)")
    return 0


def cmd_validate_catalog(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    failures = catalog_repo_failures(manifest, Path(args.catalog_path).resolve())
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("catalog checks passed")
    return 0


def cmd_validate_github(args: argparse.Namespace) -> int:
    failures = validate_github_policy(
        Path(args.policy),
        repos=args.repo,
        check_secrets=args.check_secrets,
    )
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("github policy checks passed")
    return 0


def cmd_check_run(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repo = _repo_for_identifier(manifest, args.repo)
    conclusion = args.conclusion
    if args.status == "completed" and conclusion is None:
        conclusion = "success"
    payload = check_run_payload(
        repo,
        sha=args.sha,
        event=args.event,
        status=args.status,
        conclusion=conclusion,
        summary=args.summary,
        details_url=args.details_url,
    )
    if args.dry_run:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    result = upsert_check_run(
        repo,
        sha=args.sha,
        event=args.event,
        status=args.status,
        conclusion=conclusion,
        summary=args.summary,
        details_url=args.details_url,
    )
    print(
        json.dumps(
            {
                "action": result.action,
                "check_run_id": result.check_run_id,
                "html_url": result.html_url,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def cmd_poll(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    targets = poll_targets(
        manifest,
        include_prs=not args.no_prs,
        include_main=not args.no_main,
    )
    emitted: list[dict[str, object]] = []
    for target in targets:
        if args.missing_checks_only and check_run_satisfied(
            target.repo, sha=target.sha, event=target.event
        ):
            continue
        row = {
            "repo": target.repo.name,
            "sha": target.sha,
            "event": target.event,
            "source": target.source,
            "publish": target.event == "push"
            and target.repo.publish_profile != "template",
        }
        emitted.append(row)
        if args.create_checks:
            if args.dry_run:
                row["check_payload"] = check_run_payload(
                    target.repo,
                    sha=target.sha,
                    event=target.event,
                    status="queued",
                    summary=f"Queued from aio-fleet poll source {target.source}",
                )
            else:
                result = upsert_check_run(
                    target.repo,
                    sha=target.sha,
                    event=target.event,
                    status="queued",
                    summary=f"Queued from aio-fleet poll source {target.source}",
                )
                row["check_run"] = {
                    "action": result.action,
                    "check_run_id": result.check_run_id,
                    "html_url": result.html_url,
                }
    if args.format == "json":
        print(json.dumps({"targets": emitted}, indent=2, sort_keys=True))
    else:
        for row in emitted:
            print(f"{row['repo']} {row['source']} {row['event']} {row['sha']}")
        print(f"poll targets: {len(emitted)}")
    return 0


def cmd_alert_send(args: argparse.Namespace) -> int:
    report = None
    if args.report_json:
        report = json.loads(Path(args.report_json).read_text())

    if report is not None:
        payload = payload_from_report(
            event=args.event,
            report=report,
            status=args.status,
            summary=args.summary,
            details_url=args.details_url or "",
            dedupe_key=args.dedupe_key or "",
        )
    else:
        status = "success" if args.status == "auto" else args.status
        payload = alert_payload(
            event=args.event,
            status=status,
            summary=args.summary,
            repo=args.repo or "",
            component=args.component or "",
            details_url=args.details_url or "",
            dedupe_key=args.dedupe_key or "",
            annotations=args.annotation or [],
        )

    result = emit_alert(
        payload,
        kuma_url=args.kuma_url or os.environ.get("AIO_FLEET_KUMA_PUSH_URL", ""),
        webhook_url=args.webhook_url
        or os.environ.get("AIO_FLEET_ALERT_WEBHOOK_URL", ""),
        webhook_format=args.webhook_format,
        force_webhook=args.force_webhook,
        dry_run=args.dry_run,
    )
    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{payload.event}: alert={payload.status}")
        print(f"kuma: {result['kuma']}")
        print(f"webhook: {result['webhook']}")
    return 0


def cmd_alert_doctor(args: argparse.Namespace) -> int:
    kuma_url = args.kuma_url or os.environ.get("AIO_FLEET_KUMA_PUSH_URL", "")
    webhook_url = args.webhook_url or os.environ.get("AIO_FLEET_ALERT_WEBHOOK_URL", "")
    findings: list[str] = []
    warnings: list[str] = []
    if not kuma_url:
        warnings.append("AIO_FLEET_KUMA_PUSH_URL is not configured")
    if not webhook_url:
        warnings.append("AIO_FLEET_ALERT_WEBHOOK_URL is not configured")
    if args.require_alerts:
        findings.extend(warnings)
        warnings = []
    report = {
        "kuma": "configured" if kuma_url else "missing",
        "webhook": "configured" if webhook_url else "missing",
        "warnings": warnings,
        "findings": findings,
        "ok": not findings,
    }
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"alert kuma={report['kuma']} webhook={report['webhook']}")
        for warning in warnings:
            print(f"- warning: {warning}")
        for finding in findings:
            print(f"- {finding}")
    return 1 if findings else 0


def cmd_alert_test(args: argparse.Namespace) -> int:
    payload = alert_payload(
        event=args.event,
        status=args.status,
        summary=args.summary,
        repo=args.repo or "",
        component=args.component or "",
        dedupe_key=args.dedupe_key or "alert-test:fleet:all",
        details_url=args.details_url or "",
        annotations=["aio-fleet alert test"],
    )
    result = emit_alert(
        payload,
        kuma_url=args.kuma_url or os.environ.get("AIO_FLEET_KUMA_PUSH_URL", ""),
        webhook_url=args.webhook_url
        or os.environ.get("AIO_FLEET_ALERT_WEBHOOK_URL", ""),
        webhook_format=args.webhook_format,
        force_webhook=True,
        dry_run=args.dry_run,
    )
    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{payload.event}: alert-test={payload.status}")
        print(f"kuma: {result['kuma']}")
        print(f"webhook: {result['webhook']}")
    return 0


def cmd_fleet_dashboard_update(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    report = dashboard_report(
        manifest,
        include_registry=args.registry,
        issue_repo=args.issue_repo,
    )
    result = upsert_dashboard_issue(
        issue_repo=args.issue_repo,
        body=str(report["body"]),
        dry_run=not args.write,
    )
    output = {
        "action": result.action,
        "issue_number": result.number,
        "issue_url": result.url,
        "state": report["state"],
    }
    if args.format == "json":
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print(f"fleet-dashboard: {result.action}")
        if result.url:
            print(result.url)
        if not args.write:
            print(str(report["body"]))
    return 0


def cmd_control_check(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repo = _repo_for_identifier(manifest, args.repo)
    if args.repo_path:
        repo = _repo_with_path(repo, Path(args.repo_path).resolve())
    steps = central_check_steps(
        repo,
        event=args.event,
        manifest_path=Path(args.manifest).resolve(),
        publish=args.publish,
        include_trunk=not args.no_trunk,
        include_integration=not args.no_integration,
    )
    if args.check_run:
        status = "completed" if args.dry_run else "in_progress"
        summary = "aio-fleet central check started"
        if args.dry_run:
            print(
                json.dumps(
                    check_run_payload(
                        repo,
                        sha=args.sha,
                        event=args.event,
                        status=status,
                        conclusion="success" if status == "completed" else None,
                        summary="dry-run central check plan",
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            upsert_check_run(
                repo,
                sha=args.sha,
                event=args.event,
                status=status,
                summary=summary,
            )
    failures = run_steps(steps, dry_run=args.dry_run)
    if args.check_run and not args.dry_run:
        conclusion = "failure" if failures else "success"
        upsert_check_run(
            repo,
            sha=args.sha,
            event=args.event,
            status="completed",
            conclusion=conclusion,
            summary=(
                "\n".join(failures) if failures else "aio-fleet central check passed"
            ),
        )
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


def cmd_catalog_audit(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    findings = catalog_quality_findings(manifest, Path(args.catalog_path).resolve())
    report = {
        "catalog_path": str(Path(args.catalog_path).resolve()),
        "findings": findings,
        "summary": {"findings": len(findings)},
    }
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.format == "markdown":
        print("# Catalog Audit")
        print()
        if findings:
            for finding in findings:
                print(f"- {finding}")
        else:
            print("No catalog audit findings.")
    else:
        if findings:
            print("\n".join(findings))
        else:
            print("catalog audit passed")
    return 1 if findings else 0


def cmd_registry_verify(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    if not args.all and not args.repo:
        print("--repo is required unless --all is used", file=sys.stderr)
        return 1
    repos = (
        list(manifest.repos.values())
        if args.all
        else [_repo_for_identifier(manifest, args.repo)]
    )
    if args.repo_path:
        if len(repos) != 1:
            print("--repo-path can only be used with --repo", file=sys.stderr)
            return 1
        repos = [_repo_with_path(repos[0], Path(args.repo_path).resolve())]
    failures: list[str] = []
    report: dict[str, object] = {"repos": []}
    for repo in repos:
        if args.all and repo.publish_profile == "template" and not args.include_manual:
            report["repos"].append(  # type: ignore[index]
                {
                    "repo": repo.name,
                    "sha": _git_head(repo.path),
                    "dockerhub": [],
                    "ghcr": [],
                    "failures": [],
                    "skipped": "manual-template-publish",
                }
            )
            continue
        sha = args.sha or _git_head(repo.path)
        components = publish_components(repo) if args.all else [args.component]
        for component in components:
            tags = compute_registry_tags(repo, sha=sha, component=component)
            repo_failures = [] if args.dry_run else verify_registry_tags(tags.all_tags)
            failures.extend(
                f"{repo.name}:{component}: {failure}" for failure in repo_failures
            )
            report["repos"].append(  # type: ignore[index]
                {
                    "repo": repo.name,
                    "component": component,
                    "sha": sha,
                    "dockerhub": tags.dockerhub,
                    "ghcr": tags.ghcr,
                    "failures": repo_failures,
                }
            )
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for item in report["repos"]:  # type: ignore[index]
            component = str(item.get("component", "aio"))  # type: ignore[union-attr]
            label = (
                str(item["repo"])  # type: ignore[index]
                if component == "aio"
                else f"{item['repo']}:{component}"  # type: ignore[index]
            )
            if item.get("skipped"):  # type: ignore[union-attr]
                print(
                    f"{label}: registry=skipped:{item['skipped']}"  # type: ignore[index]
                )
                continue
            state = "failed" if item["failures"] else "ok"  # type: ignore[index]
            print(f"{label}: registry={state}")
            if args.verbose or args.dry_run:
                for tag in [*item["dockerhub"], *item["ghcr"]]:  # type: ignore[index]
                    print(f"- {tag}")
        if failures:
            print("\n".join(failures), file=sys.stderr)
    return 1 if failures else 0


def cmd_registry_publish(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repo = _repo_for_identifier(manifest, args.repo)
    if args.repo_path:
        repo = _repo_with_path(repo, Path(args.repo_path).resolve())
    sha = args.sha or _git_head(repo.path)
    command = registry_publish_command(repo, sha=sha, component=args.component)
    if args.dry_run:
        print(" ".join(shlex.quote(part) for part in command))
        return 0
    preflight = cmd_registry_verify(
        argparse.Namespace(
            manifest=args.manifest,
            all=False,
            repo=args.repo,
            repo_path=args.repo_path,
            sha=sha,
            component=args.component,
            include_manual=True,
            dry_run=False,
            format="text",
            verbose=False,
        )
    )
    if preflight == 0:
        print(f"{repo.name}:{args.component}: registry=already-current")
        return 0
    result = _run(command, cwd=repo.path)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode != 0:
        return result.returncode
    return cmd_registry_verify(
        argparse.Namespace(
            manifest=args.manifest,
            all=False,
            repo=args.repo,
            repo_path=args.repo_path,
            sha=sha,
            component=args.component,
            include_manual=True,
            dry_run=False,
            format="text",
            verbose=True,
        )
    )


def cmd_upstream_monitor(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    if not args.all and not args.repo:
        print("--repo is required unless --all is used", file=sys.stderr)
        return 1
    repos = (
        list(manifest.repos.values())
        if args.all
        else [_repo_for_identifier(manifest, args.repo)]
    )
    if args.repo_path:
        if len(repos) != 1:
            print("--repo-path can only be used with --repo", file=sys.stderr)
            return 1
        repos = [_repo_with_path(repos[0], Path(args.repo_path).resolve())]

    report: dict[str, object] = {"repos": []}
    failed = False
    for repo in repos:
        if repo.publish_profile == "template" and not args.include_manual:
            report["repos"].append(  # type: ignore[index]
                {"repo": repo.name, "skipped": "manual-template"}
            )
            continue
        try:
            results = monitor_repo(repo, write=args.write and not args.dry_run)
            if (
                args.write
                and not args.dry_run
                and any(result.updates_available for result in results)
            ):
                _run_generator_for_write(repo)
            actions: list[dict[str, object]] = []
            if args.create_pr and any(result.updates_available for result in results):
                actions.append(
                    create_or_update_upstream_pr(
                        repo,
                        results,
                        dry_run=args.dry_run,
                        post_check=args.post_check,
                    )
                )
            report["repos"].append(  # type: ignore[index]
                {
                    "repo": repo.name,
                    "results": [result_dict(result) for result in results],
                    "actions": actions,
                }
            )
        except Exception as exc:
            failed = True
            report["repos"].append(  # type: ignore[index]
                {"repo": repo.name, "error": str(exc)}
            )

    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for item in report["repos"]:  # type: ignore[index]
            if item.get("skipped"):  # type: ignore[union-attr]
                print(f"{item['repo']}: upstream=skipped:{item['skipped']}")  # type: ignore[index]
                continue
            if item.get("error"):  # type: ignore[union-attr]
                print(f"{item['repo']}: upstream=failed:{item['error']}")  # type: ignore[index]
                continue
            results = item["results"]  # type: ignore[index]
            updates = [
                result
                for result in results
                if result["updates_available"]  # type: ignore[index]
            ]
            state = "updates" if updates else "ok"
            print(f"{item['repo']}: upstream={state}")  # type: ignore[index]
            for result in results:
                print(
                    "- {component}: {current_version} -> {latest_version} "
                    "version_update={version_update} digest_update={digest_update}".format(
                        **result
                    )
                )
    return 1 if failed else 0


def _run_generator_for_write(repo: RepoConfig) -> None:
    generator = str(repo.get("generator_check_command", "") or "").strip()
    if not generator:
        return
    command = [part for part in shlex.split(generator) if part != "--check"]
    result = _run(command, cwd=repo.path)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{repo.name}: generator update failed: {detail}")


def cmd_release_readiness(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repo = _repo_for_identifier(manifest, args.repo)
    catalog_path = Path(args.catalog_path).resolve() if args.catalog_path else None
    policy_path = Path(args.policy)
    findings: list[str] = []
    warnings: list[str] = []

    status = _run(["git", "status", "--short"], cwd=repo.path)
    if status.stdout.strip():
        findings.append(f"{repo.name}: worktree is dirty")

    drift = _run(
        ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"],
        cwd=repo.path,
    )
    ahead = behind = "?"
    if drift.returncode == 0:
        ahead, behind = (drift.stdout.strip().split() + ["0", "0"])[:2]
        if behind != "0":
            findings.append(f"{repo.name}: branch is behind origin/main by {behind}")
    else:
        warnings.append(f"{repo.name}: unable to inspect branch drift")

    open_prs = _open_prs(repo)
    if open_prs not in {"0", "not-run", "unknown"}:
        findings.append(f"{repo.name}: has {open_prs} open PR(s)")

    try:
        policy_repos = set(load_policy(policy_path)["repositories"])
        if repo.name in policy_repos:
            findings.extend(
                validate_github_policy(
                    policy_path, repos=[repo.name], check_secrets=False
                )
            )
        else:
            warnings.append(f"{repo.name}: github policy is manual")
    except Exception as exc:
        warnings.append(f"{repo.name}: unable to validate GitHub policy: {exc}")

    if catalog_path:
        catalog_failures = [
            failure
            for failure in catalog_repo_failures(manifest, catalog_path)
            if failure.startswith(f"{repo.name}:")
        ]
        findings.extend(catalog_failures)

    latest_ci = _latest_main_ci(repo)
    if latest_ci["state"] != "success":
        findings.append(f"{repo.name}: latest main CI is {latest_ci['state']}")

    release_version = _release_version(repo)
    if not release_version:
        findings.append(f"{repo.name}: unable to read latest changelog version")

    image_status = _image_status(repo)
    if image_status != "ok":
        warnings.append(f"{repo.name}: image publish status is {image_status}")

    report = {
        "repo": repo.name,
        "ahead": ahead,
        "behind": behind,
        "open_prs": open_prs,
        "latest_ci": latest_ci,
        "release_version": release_version,
        "image_status": image_status,
        "findings": findings,
        "warnings": warnings,
        "ready": not findings,
    }
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        state = "ready" if not findings else "blocked"
        print(f"{repo.name}: release-readiness={state}")
        for finding in findings:
            print(f"- {finding}")
        for warning in warnings:
            print(f"- warning: {warning}")
    return 1 if findings else 0


def _latest_main_ci(repo: RepoConfig) -> dict[str, str]:
    sha_result = _run(
        ["gh", "api", f"repos/{repo.github_repo}/commits/main", "--jq", ".sha"],
        cwd=repo.path,
    )
    if sha_result.returncode != 0:
        return {"state": "unknown", "detail": sha_result.stderr.strip()}
    sha = sha_result.stdout.strip()
    result = _run(
        [
            "gh",
            "api",
            f"repos/{repo.github_repo}/commits/{sha}/check-runs?check_name=aio-fleet%20%2F%20required",
        ],
        cwd=repo.path,
    )
    if result.returncode != 0:
        return {"state": "unknown", "detail": result.stderr.strip()}
    try:
        runs = json.loads(result.stdout or "{}").get("check_runs", [])
    except json.JSONDecodeError:
        return {"state": "unknown", "detail": "unable to parse gh run output"}
    if not runs:
        return {
            "state": "missing",
            "head_sha": sha,
            "detail": f"no {CHECK_NAME} check found",
        }
    run = runs[0]
    state = (
        "success"
        if run.get("status") == "completed" and run.get("conclusion") == "success"
        else str(run.get("conclusion") or run.get("status") or "unknown")
    )
    return {
        "state": state,
        "head_sha": sha,
        "url": str(run.get("html_url") or ""),
    }


def _release_version(repo: RepoConfig) -> str:
    try:
        return latest_changelog_version(
            repo.path / "CHANGELOG.md", semver=repo.publish_profile == "template"
        )
    except Exception:
        return ""


def _image_status(repo: RepoConfig) -> str:
    docker = shutil.which("docker")
    if docker is None:
        return "unknown:no-docker"
    result = _run(
        [docker, "manifest", "inspect", f"{repo.image_name}:latest"],
        cwd=repo.path,
    )
    return "ok" if result.returncode == 0 else "unknown:latest-not-inspected"


def cmd_release_status(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repo = _repo_for_identifier(manifest, args.repo)
    if args.repo_path:
        repo = _repo_with_path(repo, Path(args.repo_path).resolve())
    plan = build_release_plan(repo)
    tags = compute_registry_tags(repo, sha=_git_head(repo.path))
    report = {
        "repo": repo.name,
        "version": plan.version,
        "changelog": str(plan.changelog_path),
        "xml_paths": [str(path) for path in plan.xml_paths],
        "registry_tags": tags.all_tags,
    }
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"{repo.name}: next_release={plan.version}")
        print(f"changelog: {plan.changelog_path}")
        for path in plan.xml_paths:
            print(f"xml: {path}")
    return 0


def cmd_release_prepare(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repo = _repo_for_identifier(manifest, args.repo)
    if args.repo_path:
        repo = _repo_with_path(repo, Path(args.repo_path).resolve())
    plan = build_release_plan(repo)
    cliff_config = write_temp_git_cliff_config(repo)
    commands = [
        [
            "git",
            "cliff",
            "--config",
            str(cliff_config),
            "--tag",
            plan.version,
            "--output",
            str(plan.changelog_path),
        ]
    ]
    if args.dry_run:
        print(f"{repo.name}: would prepare release {plan.version}")
        for command in commands:
            print(" ".join(shlex.quote(part) for part in command))
        for xml_path in plan.xml_paths:
            print(f"would update <Changes> in {xml_path}")
        return 0
    for command in commands:
        result = _run(command, cwd=repo.path)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        if result.returncode != 0:
            return result.returncode
    for xml_path in plan.xml_paths:
        update_template_changes(
            version=plan.version,
            changelog=plan.changelog_path,
            template=xml_path,
        )
        print(f"updated {xml_path}")
    return 0


def cmd_release_publish(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repo = _repo_for_identifier(manifest, args.repo)
    if args.repo_path:
        repo = _repo_with_path(repo, Path(args.repo_path).resolve())
    latest_version = latest_changelog_version(
        repo.path / "CHANGELOG.md", semver=repo.publish_profile == "template"
    )
    release_target = find_release_target_commit(repo.path, latest_version)
    notes = _run(
        [
            sys.executable,
            "-m",
            "aio_fleet.release",
            "--repo-path",
            str(repo.path),
            "--release-profile",
            "semver" if repo.publish_profile == "template" else "aio",
            "extract-release-notes",
            latest_version,
        ],
        cwd=repo.path,
    )
    if notes.returncode != 0:
        print(notes.stderr or notes.stdout, file=sys.stderr, end="")
        return notes.returncode
    command = [
        "gh",
        "release",
        "create",
        latest_version,
        "--repo",
        repo.github_repo,
        "--target",
        release_target,
        "--title",
        latest_version,
        "--notes",
        notes.stdout.strip(),
    ]
    if args.dry_run:
        print(" ".join(shlex.quote(part) for part in command))
        return 0
    result = _run(command, cwd=repo.path)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def cmd_validate(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repos = manifest.repos.values() if args.all else [manifest.repo(args.repo)]
    failed = False
    for repo in repos:
        print(f"== {repo.name} ==")
        failures = [
            *_app_manifest_failures(repo),
            *repo_policy_failures(repo, manifest),
        ]
        if failures:
            print("\n".join(failures), file=sys.stderr)
            failed = True
            continue

        generator = str(repo.get("generator_check_command", "") or "").strip()
        if generator:
            result = _run(shlex.split(generator), cwd=repo.path)
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
            if result.returncode != 0:
                failed = True
                continue
        print(f"{repo.name} central validation passed")
    return 1 if failed else 0


def cmd_cleanup_repo(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    if not args.all and not args.repo:
        print("--repo is required unless --all is used", file=sys.stderr)
        return 1
    repos = (
        list(manifest.repos.values())
        if args.all
        else [_repo_for_identifier(manifest, args.repo)]
    )
    if args.repo_path:
        if len(repos) != 1:
            print("--repo-path can only be used with --repo", file=sys.stderr)
            return 1
        repos = [_repo_with_path(repos[0], Path(args.repo_path).resolve())]
    failed = False
    report: dict[str, object] = {"repos": []}
    for repo in repos:
        findings = cleanup_findings(repo)
        if findings and (args.remove or args.fix) and not args.dry_run:
            remove_cleanup_findings(findings)
            findings = cleanup_findings(repo)
        if args.verify and findings:
            failed = True
        report["repos"].append(  # type: ignore[index]
            {
                "repo": repo.name,
                "findings": [
                    {
                        "path": str(finding.path.relative_to(repo.path)),
                        "reason": finding.reason,
                    }
                    for finding in findings
                ],
            }
        )
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for item in report["repos"]:  # type: ignore[index]
            findings = item["findings"]  # type: ignore[index]
            state = "ok" if not findings else f"{len(findings)} retired shared paths"
            print(f"{item['repo']}: cleanup={state}")  # type: ignore[index]
            for finding in findings:
                print(f"- {finding['path']}: {finding['reason']}")
    return 1 if failed else 0


def cmd_trunk_audit(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repos = [manifest.repo(args.repo)] if args.repo else manifest.repos.values()
    failed = False
    for repo in repos:
        trunk_config = repo.path / ".trunk" / "trunk.yaml"
        if not trunk_config.exists():
            print(f"{repo.name}: trunk=skipped:no-config")
            continue
        command = [
            "trunk",
            "check",
            "--show-existing",
            "--all",
            "--no-fix",
            "--no-progress",
            "--color=false",
        ]
        result = _run(command, cwd=repo.path)
        if result.returncode == 0:
            print(f"{repo.name}: trunk=ok")
            continue
        failed = True
        issue_line = next(
            (
                line.strip()
                for line in result.stdout.splitlines()
                if "issues" in line.lower()
            ),
            "",
        )
        detail = f" {issue_line}" if issue_line else ""
        print(f"{repo.name}: trunk=failed exit={result.returncode}{detail}")
        if args.verbose:
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
    return 1 if failed else 0


def cmd_trunk_run(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    if not args.all and not args.repo:
        print("--repo is required unless --all is used", file=sys.stderr)
        return 1
    repos = (
        list(manifest.repos.values())
        if args.all
        else [_repo_for_identifier(manifest, args.repo)]
    )
    if args.repo_path:
        if len(repos) != 1:
            print("--repo-path can only be used with --repo", file=sys.stderr)
            return 1
        repos = [_repo_with_path(repos[0], Path(args.repo_path).resolve())]
    failed = False
    for repo in repos:
        result = run_central_trunk(repo, fix=args.fix)
        if result.returncode == 0:
            print(f"{repo.name}: trunk=ok")
            continue
        failed = True
        print(f"{repo.name}: trunk=failed exit={result.returncode}")
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
    return 1 if failed else 0


def cmd_import_app_manifest(args: argparse.Namespace) -> int:
    path = Path(args.path).resolve()
    data = load_app_manifest(path)
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


def cmd_sync_catalog(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repos = (
        [_repo_for_identifier(manifest, args.repo)]
        if args.repo
        else list(manifest.repos.values())
    )
    if args.repo_path:
        if len(repos) != 1:
            print("--repo-path can only be used with --repo", file=sys.stderr)
            return 1
        repos = [_repo_with_path(repos[0], Path(args.repo_path).resolve())]
    blocked = unpublished_xml_targets(manifest, repos)
    if blocked and not args.icon_only:
        print(
            "refusing XML sync for catalog_published=false repos; use --icon-only for staged launches:\n"
            + "\n".join(blocked),
            file=sys.stderr,
        )
        return 1

    changes = sync_catalog_assets(
        manifest,
        catalog_path=Path(args.catalog_path).resolve(),
        repos=repos,
        icon_only=args.icon_only,
        dry_run=args.dry_run,
    )
    for change in changes:
        prefix = "would " if args.dry_run else ""
        print(
            f"{change.repo}: {prefix}{change.action} "
            f"{change.target.relative_to(Path(args.catalog_path).resolve())}"
        )
    print(f"catalog changes: {len(changes)}")
    if changes and args.create_pr:
        _catalog_commit_and_pr(
            Path(args.catalog_path).resolve(),
            branch=args.branch or _catalog_branch(args.repo, args.icon_only),
            base=args.base,
            title=args.title or _catalog_title(args.repo, args.icon_only),
            body=args.body or _catalog_body(args.repo, args.icon_only),
            paths=[change.target for change in changes],
            dry_run=args.dry_run,
        )
    return 0


def cmd_infra_doctor(args: argparse.Namespace) -> int:
    root = Path.cwd()
    infra_path = Path(args.path).resolve()
    manifest = load_manifest(Path(args.manifest))
    policy_path = Path(args.policy)
    failures: list[str] = []

    if not args.skip_tofu and shutil.which("tofu") is None:
        failures.append("OpenTofu CLI is not installed")
    if not (infra_path / ".terraform.lock.hcl").exists():
        failures.append(f"{infra_path}: missing .terraform.lock.hcl")
    if not (infra_path / ".terraform").exists():
        failures.append(f"{infra_path}: OpenTofu is not initialized; run tofu init")

    failures.extend(tracked_artifact_failures(root))
    for relative in [
        "infra/github/terraform.tfstate",
        "infra/github/terraform.tfstate.backup",
        "infra/github/repos.tfvars",
    ]:
        if not _git_check_ignore(root, relative):
            failures.append(f"{relative} is not ignored by git")

    try:
        policy = load_policy(policy_path)
        policy_repos = set(policy["repositories"])
        expected = {
            repo.name
            for repo in manifest.repos.values()
            if repo.raw.get("public") is not False
        }
        expected.add("awesome-unraid")
        missing = sorted(expected - policy_repos)
        extra = sorted(policy_repos - expected)
        if missing:
            failures.append(f"github policy missing repos: {missing}")
        if extra:
            failures.append(f"github policy has unknown repos: {extra}")
    except Exception as exc:
        failures.append(f"unable to load GitHub policy: {exc}")

    if not args.skip_tofu and shutil.which("tofu") is not None:
        for command in (["tofu", "fmt", "-check", "-recursive"], ["tofu", "validate"]):
            result = _run(command, cwd=infra_path)
            if result.returncode != 0:
                failures.append(
                    f"{' '.join(command)} failed: {(result.stderr or result.stdout).strip()}"
                )

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("infra doctor passed")
    return 0


def _git_check_ignore(repo_path: Path, relative_path: str) -> bool:
    result = _run(["git", "check-ignore", "--quiet", relative_path], cwd=repo_path)
    return result.returncode == 0


def cmd_onboard_repo(args: argparse.Namespace) -> int:
    image_name = args.image_name or f"jsonbored/{args.repo}"
    upstream_name = args.upstream_name or args.repo.removesuffix("-aio").title()
    local_path_base = args.local_path_base.rstrip("/")
    manifest_entry = {
        "path": f"{local_path_base}/{args.repo}",
        "public": True,
        "workflow_name": f"CI / {upstream_name} AIO",
        "app_slug": args.repo,
        "image_name": image_name,
        "docker_cache_scope": f"{args.repo}-image",
        "pytest_image_tag": f"{args.repo}:pytest",
        "publish_profile": args.profile,
        "release_name": f"{upstream_name} AIO",
        "upstream_name": upstream_name,
        "image_description": f"Unraid-first AIO wrapper image for {upstream_name}",
        "xml_paths": [f"{args.repo}.xml"],
        "catalog_assets": [
            {"source": f"{args.repo}.xml", "target": f"{args.repo}.xml"}
        ],
    }
    acceptance_checklist = [
        "repo created from unraid-aio-template",
        "fleet.yml entry added with Docker Hub image name",
        ".aio-fleet.yml exported into the app repo",
        "central repo validation passes",
        "cleanup-repo --verify reports no retired shared files",
        "control-check dry-run succeeds for the app commit",
        "upstream monitor dry-run resolves provider state",
        "registry verify dry-run prints expected Docker Hub and GHCR tags",
        "catalog sync dry-run shows expected XML/icon assets",
        "support-thread render produces a CA-ready draft",
        "aio-fleet / required appears on a real app PR",
    ]
    if args.format == "json":
        print(
            json.dumps(
                {
                    "repo": args.repo,
                    "manifest_entry": manifest_entry,
                    "acceptance_checklist": acceptance_checklist,
                },
                indent=2,
            )
        )
        return 0

    print(f"# Onboard {args.repo}")
    print()
    print("## Manifest entry")
    print()
    print("```yaml")
    print(f"  {args.repo}:")
    for key, value in manifest_entry.items():
        print(_yaml_line(key, value, indent=4))
    print("```")
    print()
    print("## First commands")
    print()
    print(f"- `python -m aio_fleet export-app-manifest --repo {args.repo} --write`")
    print(
        f"- `python -m aio_fleet validate-repo --repo {args.repo} --repo-path ../{args.repo}`"
    )
    print(
        f"- `python -m aio_fleet sync-catalog --repo {args.repo} --catalog-path ../awesome-unraid --dry-run`"
    )
    print(f"- `python -m aio_fleet upstream monitor --repo {args.repo} --dry-run`")
    print(
        f"- `python -m aio_fleet registry verify --repo {args.repo} --sha <commit-sha> --dry-run --verbose`"
    )
    print(f"- `python -m aio_fleet support-thread render --repo {args.repo}`")
    print()
    print("## Acceptance checklist")
    print()
    for item in acceptance_checklist:
        print(f"- [ ] {item}")
    return 0


def cmd_export_app_manifest(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repo = _repo_for_identifier(manifest, args.repo)
    rendered = render_app_manifest(repo)
    output = (
        Path(args.output).resolve() if args.output else repo.path / APP_MANIFEST_NAME
    )
    if args.write:
        output.write_text(rendered)
        print(f"wrote {output}")
        return 0
    print(rendered, end="")
    return 0


def _yaml_line(key: str, value: object, *, indent: int) -> str:
    prefix = " " * indent
    if isinstance(value, list):
        lines = [f"{prefix}{key}:"]
        for item in value:
            if isinstance(item, dict):
                entries = list(item.items())
                if not entries:
                    lines.append(f"{prefix}  - {{}}")
                    continue
                first_key, first_value = entries[0]
                lines.append(f"{prefix}  - {first_key}: {first_value}")
                for item_key, item_value in entries[1:]:
                    lines.append(f"{prefix}    {item_key}: {item_value}")
            else:
                lines.append(f"{prefix}  - {item}")
        return "\n".join(lines)
    if isinstance(value, bool):
        return f"{prefix}{key}: {str(value).lower()}"
    return f"{prefix}{key}: {value}"


def cmd_support_thread_render(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repo = _repo_for_identifier(manifest, args.repo)
    xml_path = _first_xml_source(repo)
    xml_values = _xml_text_values(repo.path / xml_path) if xml_path else {}
    template_path = (
        Path(__file__).resolve().parents[2] / "docs" / "support-thread-template.md"
    )
    text = template_path.read_text()
    replacements = {
        "{{APP_NAME}}": repo.app_slug,
        "{{SHORT_DESCRIPTOR}}": repo.get("upstream_name", repo.app_slug),
        "{{ONE_SENTENCE_APP_DESCRIPTION}}": _app_description(
            _first_sentence(xml_values.get("Overview", "")),
            str(repo.get("upstream_name", repo.app_slug)),
        ),
        "{{UPSTREAM_APP_NAME}}": str(repo.get("upstream_name", repo.app_slug)),
        "{{IMAGE_NAME}}": repo.image_name,
        "{{WEBUI_URL_OR_NOTE}}": _webui_note(xml_values),
        "{{APPDATA_PATHS}}": "/appdata",
        "{{REQUIRED_FIELDS}}": "Use the default template values unless the app README says otherwise.",
        "{{FIRST_BOOT_EXPECTATIONS}}": "First boot may take several minutes while bundled services initialize.",
        "{{LIMITATION_1}}": "AIO packaging trades service separation for easier Unraid installation.",
        "{{LIMITATION_2}}": "Advanced external dependencies remain app-specific.",
        "{{LIMITATION_3}}": "Back up appdata before upgrades.",
        "{{PATH_1}}": "/appdata",
        "{{PATH_2}}": "See the Unraid template for app-specific persisted paths.",
        "{{PATH_3}}": "See the source README for optional external service paths.",
        "{{PROJECT_REPO_URL}}": f"https://github.com/{repo.github_repo}",
        "{{UPSTREAM_URL}}": xml_values.get("Project", ""),
        "{{CATALOG_REPO_URL}}": "https://github.com/JSONbored/awesome-unraid",
        "{{GITHUB_SPONSORS_URL}}": "https://github.com/sponsors/JSONbored",
        "{{KOFI_URL}}": "https://ko-fi.com/jsonbored",
        "{{MAINTAINER_NAME}}": "JSONbored",
        "{{GITHUB_PROFILE_URL}}": "https://github.com/JSONbored",
        "{{PORTFOLIO_URL}}": "https://aethereal.dev",
    }
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, str(value))
    print(text)
    return 0


def _first_xml_source(repo: RepoConfig) -> str:
    assets = repo.raw.get("catalog_assets", [])
    if not isinstance(assets, list):
        return ""
    for asset in assets:
        if isinstance(asset, dict):
            source = str(asset.get("source", ""))
            if source.endswith(".xml"):
                return source
    return ""


def _xml_text_values(xml_path: Path) -> dict[str, str]:
    if not xml_path.exists():
        return {}
    import defusedxml.ElementTree as ET

    root = ET.parse(xml_path).getroot()
    return {child.tag: (child.text or "").strip() for child in root}


def _first_sentence(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return ""
    sentence = cleaned.split(". ", 1)[0].strip()
    return sentence if sentence.endswith(".") else f"{sentence}."


def _app_description(sentence: str, upstream_name: str) -> str:
    prefix = f"{upstream_name} is "
    if sentence.startswith(prefix):
        return sentence[len(prefix) :].rstrip(".")
    return (sentence[:1].lower() + sentence[1:]).rstrip(".") if sentence else ""


def _webui_note(values: dict[str, str]) -> str:
    for key, value in values.items():
        if key == "WebUI" and value:
            return value
    return "Open the Web UI from the Unraid Docker page after the container starts."


def _catalog_branch(repo: str | None, icon_only: bool) -> str:
    if repo:
        suffix = "icons" if icon_only else "catalog-assets"
        return f"sync-awesome-unraid/{repo}-{suffix}"
    return "sync-awesome-unraid/fleet-catalog-assets"


def _catalog_title(repo: str | None, icon_only: bool) -> str:
    if repo:
        target = "icons" if icon_only else "catalog assets"
        return f"ci(sync): sync {repo} {target}"
    return "ci(sync): sync fleet catalog assets"


def _catalog_body(repo: str | None, icon_only: bool) -> str:
    scope = f"`{repo}` " if repo else "fleet "
    mode = "icon-only staged launch sync" if icon_only else "catalog XML/icon sync"
    return f"""## Summary
- Syncs {scope}assets into `awesome-unraid`.

## What changed
- Runs `aio-fleet sync-catalog`
- Mode: {mode}

## Why
- Keeps Community Apps catalog assets aligned with the source repo manifest.

## Validation
- Generated by `aio-fleet`; catalog validation should run on this PR.
"""


def _catalog_commit_and_pr(
    catalog_path: Path,
    *,
    branch: str,
    base: str,
    title: str,
    body: str,
    paths: list[Path],
    dry_run: bool,
) -> None:
    relative_paths = [str(path.relative_to(catalog_path)) for path in paths]
    commands = [
        ["git", "config", "user.name", "github-actions[bot]"],
        [
            "git",
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        ],
        ["git", "checkout", "-B", branch],
        ["git", "add", *relative_paths],
        ["git", "commit", "-m", title],
        ["git", "push", "-u", "origin", branch],
    ]
    for command in commands:
        if dry_run:
            print(f"would run {' '.join(command)}")
            continue
        result = _run(command, cwd=catalog_path)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        if result.returncode != 0:
            raise RuntimeError(f"catalog command failed: {' '.join(command)}")

    if dry_run:
        print(f"would open or update PR {branch} -> {base}")
        return

    existing = _run(
        [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--base",
            base,
            "--json",
            "number",
            "--jq",
            ".[0].number // empty",
        ],
        cwd=catalog_path,
    )
    number = existing.stdout.strip()
    if number:
        result = _run(
            ["gh", "pr", "edit", number, "--title", title, "--body", body],
            cwd=catalog_path,
        )
    else:
        result = _run(
            [
                "gh",
                "pr",
                "create",
                "--base",
                base,
                "--head",
                branch,
                "--title",
                title,
                "--body",
                body,
            ],
            cwd=catalog_path,
        )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode != 0:
        raise RuntimeError("catalog PR command failed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aio-fleet")
    parser.add_argument("--manifest", default="fleet.yml")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--github", action="store_true")
    doctor.add_argument("--policy", default="infra/github/github-policy.yml")
    doctor.add_argument("--check-secrets", action="store_true")
    doctor.set_defaults(func=cmd_doctor)
    status = sub.add_parser("status")
    status.add_argument("--github", action="store_true")
    status.add_argument("--policy", default="infra/github/github-policy.yml")
    status.add_argument("--catalog-path")
    status.set_defaults(func=cmd_status)

    debt = sub.add_parser("debt-report")
    debt.add_argument("--catalog-path")
    debt.add_argument("--github", action="store_true")
    debt.add_argument("--policy", default="infra/github/github-policy.yml")
    debt.add_argument("--trunk", action="store_true")
    debt.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    debt.set_defaults(func=cmd_debt_report)

    sync_catalog = sub.add_parser("sync-catalog")
    sync_catalog.add_argument("--repo")
    sync_catalog.add_argument("--repo-path")
    sync_catalog.add_argument("--catalog-path", required=True)
    sync_catalog.add_argument("--icon-only", action="store_true")
    sync_catalog.add_argument("--create-pr", action="store_true")
    sync_catalog.add_argument("--branch")
    sync_catalog.add_argument("--base", default="main")
    sync_catalog.add_argument("--title")
    sync_catalog.add_argument("--body")
    sync_catalog.add_argument("--dry-run", action="store_true")
    sync_catalog.set_defaults(func=cmd_sync_catalog)

    actions = sub.add_parser("validate-actions")
    actions.add_argument("--repo-path", default=".")
    actions.set_defaults(func=cmd_validate_actions)

    derived = sub.add_parser("validate-derived")
    derived.add_argument("--repo-path", default=".")
    derived.add_argument("--strict-placeholders", action="store_true")
    derived.add_argument("--template-xml")
    derived.set_defaults(func=cmd_validate_derived)

    common_template = sub.add_parser("validate-template-common")
    common_template.add_argument("--repo")
    common_template.add_argument("--repo-path")
    common_template.add_argument("--all", action="store_true")
    common_template.set_defaults(func=cmd_validate_template_common)

    repo = sub.add_parser("validate-repo")
    repo.add_argument("--repo", required=True)
    repo.add_argument("--repo-path", default=".")
    repo.set_defaults(func=cmd_validate_repo)

    catalog = sub.add_parser("validate-catalog")
    catalog.add_argument("--catalog-path", required=True)
    catalog.set_defaults(func=cmd_validate_catalog)

    catalog_audit = sub.add_parser("catalog-audit")
    catalog_audit.add_argument("--catalog-path", required=True)
    catalog_audit.add_argument(
        "--format", choices=["text", "json", "markdown"], default="text"
    )
    catalog_audit.set_defaults(func=cmd_catalog_audit)

    github = sub.add_parser("validate-github")
    github.add_argument("--policy", default="infra/github/github-policy.yml")
    github.add_argument("--repo", action="append")
    github.add_argument("--check-secrets", action="store_true")
    github.set_defaults(func=cmd_validate_github)

    check = sub.add_parser("check")
    check_sub = check.add_subparsers(dest="check_command", required=True)
    check_run = check_sub.add_parser("run")
    check_run.add_argument("--repo", required=True)
    check_run.add_argument("--sha", required=True)
    check_run.add_argument(
        "--event",
        required=True,
        choices=["pull_request", "push", "release", "workflow_dispatch"],
    )
    check_run.add_argument(
        "--status", choices=["queued", "in_progress", "completed"], default="completed"
    )
    check_run.add_argument(
        "--conclusion",
        choices=[
            "action_required",
            "cancelled",
            "failure",
            "neutral",
            "skipped",
            "stale",
            "success",
            "timed_out",
        ],
    )
    check_run.add_argument("--summary", default="")
    check_run.add_argument("--details-url")
    check_run.add_argument("--dry-run", action="store_true")
    check_run.set_defaults(func=cmd_check_run)

    alert = sub.add_parser("alert")
    alert_sub = alert.add_subparsers(dest="alert_command", required=True)
    alert_send = alert_sub.add_parser("send")
    alert_send.add_argument("--event", required=True)
    alert_send.add_argument(
        "--status",
        choices=[
            "auto",
            "success",
            "failure",
            "warning",
            "info",
            "cancelled",
            "skipped",
        ],
        default="auto",
    )
    alert_send.add_argument("--summary", default="")
    alert_send.add_argument("--repo")
    alert_send.add_argument("--component")
    alert_send.add_argument("--dedupe-key")
    alert_send.add_argument("--details-url")
    alert_send.add_argument("--annotation", action="append")
    alert_send.add_argument("--report-json")
    alert_send.add_argument("--kuma-url")
    alert_send.add_argument("--webhook-url")
    alert_send.add_argument(
        "--webhook-format", choices=["json", "text"], default="json"
    )
    alert_send.add_argument("--force-webhook", action="store_true")
    alert_send.add_argument("--dry-run", action="store_true")
    alert_send.add_argument("--format", choices=["text", "json"], default="text")
    alert_send.set_defaults(func=cmd_alert_send)
    alert_doctor = alert_sub.add_parser("doctor")
    alert_doctor.add_argument("--kuma-url")
    alert_doctor.add_argument("--webhook-url")
    alert_doctor.add_argument("--require-alerts", action="store_true")
    alert_doctor.add_argument("--format", choices=["text", "json"], default="text")
    alert_doctor.set_defaults(func=cmd_alert_doctor)
    alert_test = alert_sub.add_parser("test")
    alert_test.add_argument("--event", default="upstream-update")
    alert_test.add_argument(
        "--status",
        choices=["success", "failure", "warning"],
        default="warning",
    )
    alert_test.add_argument("--summary", default="aio-fleet alert test")
    alert_test.add_argument("--repo")
    alert_test.add_argument("--component")
    alert_test.add_argument("--dedupe-key")
    alert_test.add_argument("--details-url")
    alert_test.add_argument("--kuma-url")
    alert_test.add_argument("--webhook-url")
    alert_test.add_argument(
        "--webhook-format", choices=["json", "text"], default="json"
    )
    alert_test.add_argument("--dry-run", action="store_true")
    alert_test.add_argument("--format", choices=["text", "json"], default="text")
    alert_test.set_defaults(func=cmd_alert_test)

    dashboard = sub.add_parser("fleet-dashboard")
    dashboard_sub = dashboard.add_subparsers(
        dest="fleet_dashboard_command", required=True
    )
    dashboard_update = dashboard_sub.add_parser("update")
    dashboard_update.add_argument("--issue-repo", default="JSONbored/aio-fleet")
    dashboard_update.add_argument("--write", action="store_true")
    dashboard_update.add_argument("--dry-run", action="store_false", dest="write")
    dashboard_update.add_argument("--registry", action="store_true")
    dashboard_update.add_argument("--format", choices=["text", "json"], default="text")
    dashboard_update.set_defaults(func=cmd_fleet_dashboard_update, write=False)

    poll = sub.add_parser("poll")
    poll.add_argument("--no-prs", action="store_true")
    poll.add_argument("--no-main", action="store_true")
    poll.add_argument("--create-checks", action="store_true")
    poll.add_argument("--missing-checks-only", action="store_true")
    poll.add_argument("--dry-run", action="store_true")
    poll.add_argument("--format", choices=["text", "json"], default="text")
    poll.set_defaults(func=cmd_poll)

    control = sub.add_parser("control-check")
    control.add_argument("--repo", required=True)
    control.add_argument("--repo-path")
    control.add_argument("--sha", required=True)
    control.add_argument(
        "--event",
        required=True,
        choices=["pull_request", "push", "release", "workflow_dispatch"],
    )
    control.add_argument("--publish", action="store_true")
    control.add_argument("--no-trunk", action="store_true")
    control.add_argument("--no-integration", action="store_true")
    control.add_argument("--check-run", action="store_true")
    control.add_argument("--dry-run", action="store_true")
    control.set_defaults(func=cmd_control_check)

    registry = sub.add_parser("registry")
    registry_sub = registry.add_subparsers(dest="registry_command", required=True)
    registry_verify = registry_sub.add_parser("verify")
    registry_verify.add_argument("--repo")
    registry_verify.add_argument("--repo-path")
    registry_verify.add_argument("--all", action="store_true")
    registry_verify.add_argument("--sha")
    registry_verify.add_argument("--component", default="aio")
    registry_verify.add_argument("--include-manual", action="store_true")
    registry_verify.add_argument("--dry-run", action="store_true")
    registry_verify.add_argument("--verbose", action="store_true")
    registry_verify.add_argument("--format", choices=["text", "json"], default="text")
    registry_verify.set_defaults(func=cmd_registry_verify)
    registry_publish = registry_sub.add_parser("publish")
    registry_publish.add_argument("--repo", required=True)
    registry_publish.add_argument("--repo-path")
    registry_publish.add_argument("--sha")
    registry_publish.add_argument("--component", default="aio")
    registry_publish.add_argument("--dry-run", action="store_true")
    registry_publish.set_defaults(func=cmd_registry_publish)

    upstream = sub.add_parser("upstream")
    upstream_sub = upstream.add_subparsers(dest="upstream_command", required=True)
    upstream_monitor = upstream_sub.add_parser("monitor")
    upstream_monitor.add_argument("--repo")
    upstream_monitor.add_argument("--repo-path")
    upstream_monitor.add_argument("--all", action="store_true")
    upstream_monitor.add_argument("--include-manual", action="store_true")
    upstream_monitor.add_argument("--write", action="store_true")
    upstream_monitor.add_argument("--create-pr", action="store_true")
    upstream_monitor.add_argument("--post-check", action="store_true")
    upstream_monitor.add_argument("--dry-run", action="store_true")
    upstream_monitor.add_argument("--format", choices=["text", "json"], default="text")
    upstream_monitor.set_defaults(func=cmd_upstream_monitor)

    release = sub.add_parser("release")
    release_sub = release.add_subparsers(dest="release_command", required=True)
    release_status = release_sub.add_parser("status")
    release_status.add_argument("--repo", required=True)
    release_status.add_argument("--repo-path")
    release_status.add_argument("--format", choices=["text", "json"], default="text")
    release_status.set_defaults(func=cmd_release_status)
    release_prepare = release_sub.add_parser("prepare")
    release_prepare.add_argument("--repo", required=True)
    release_prepare.add_argument("--repo-path")
    release_prepare.add_argument("--dry-run", action="store_true")
    release_prepare.set_defaults(func=cmd_release_prepare)
    release_publish = release_sub.add_parser("publish")
    release_publish.add_argument("--repo", required=True)
    release_publish.add_argument("--repo-path")
    release_publish.add_argument("--dry-run", action="store_true")
    release_publish.set_defaults(func=cmd_release_publish)

    readiness = sub.add_parser("release-readiness")
    readiness.add_argument("--repo", required=True)
    readiness.add_argument("--catalog-path")
    readiness.add_argument("--policy", default="infra/github/github-policy.yml")
    readiness.add_argument("--format", choices=["text", "json"], default="text")
    readiness.set_defaults(func=cmd_release_readiness)

    onboard = sub.add_parser("onboard-repo")
    onboard.add_argument("--repo", required=True)
    onboard.add_argument(
        "--profile",
        default="changelog-version",
        choices=[
            "template",
            "upstream-aio-track",
            "changelog-version",
            "dify",
            "signoz-suite",
        ],
    )
    onboard.add_argument("--upstream-name")
    onboard.add_argument("--image-name")
    onboard.add_argument("--local-path-base", default="<local-checkout-path>")
    onboard.add_argument("--dry-run", action="store_true")
    onboard.add_argument("--format", choices=["text", "json"], default="text")
    onboard.set_defaults(func=cmd_onboard_repo)

    export_app_manifest = sub.add_parser("export-app-manifest")
    export_app_manifest.add_argument("--repo", required=True)
    export_app_manifest.add_argument("--output")
    export_app_manifest.add_argument("--write", action="store_true")
    export_app_manifest.set_defaults(func=cmd_export_app_manifest)

    import_app_manifest = sub.add_parser("import-app-manifest")
    import_app_manifest.add_argument("--path", required=True)
    import_app_manifest.set_defaults(func=cmd_import_app_manifest)

    infra = sub.add_parser("infra")
    infra_sub = infra.add_subparsers(dest="infra_command", required=True)
    infra_doctor = infra_sub.add_parser("doctor")
    infra_doctor.add_argument("--path", default="infra/github")
    infra_doctor.add_argument("--policy", default="infra/github/github-policy.yml")
    infra_doctor.add_argument("--skip-tofu", action="store_true")
    infra_doctor.set_defaults(func=cmd_infra_doctor)

    support = sub.add_parser("support-thread")
    support_sub = support.add_subparsers(dest="support_command", required=True)
    support_render = support_sub.add_parser("render")
    support_render.add_argument("--repo", required=True)
    support_render.set_defaults(func=cmd_support_thread_render)

    validate = sub.add_parser("validate")
    validate.add_argument("--all", action="store_true")
    validate.add_argument("--repo")
    validate.set_defaults(func=cmd_validate)

    cleanup = sub.add_parser("cleanup-repo")
    cleanup.add_argument("--repo")
    cleanup.add_argument("--repo-path")
    cleanup.add_argument("--all", action="store_true")
    cleanup.add_argument("--verify", action="store_true")
    cleanup.add_argument("--remove", action="store_true")
    cleanup.add_argument(
        "--fix",
        action="store_true",
        help="Alias for --remove; deletes known retired shared files.",
    )
    cleanup.add_argument("--dry-run", action="store_true")
    cleanup.add_argument("--format", choices=["text", "json"], default="text")
    cleanup.set_defaults(func=cmd_cleanup_repo)

    trunk = sub.add_parser("trunk")
    trunk_sub = trunk.add_subparsers(dest="trunk_command", required=True)
    trunk_run = trunk_sub.add_parser("run")
    trunk_run.add_argument("--repo")
    trunk_run.add_argument("--repo-path")
    trunk_run.add_argument("--all", action="store_true")
    trunk_run.add_argument("--fix", action="store_true")
    trunk_run.add_argument("--no-fix", action="store_false", dest="fix")
    trunk_run.set_defaults(func=cmd_trunk_run, fix=False)

    trunk_audit = sub.add_parser("trunk-audit")
    trunk_audit.add_argument("--repo")
    trunk_audit.add_argument("--verbose", action="store_true")
    trunk_audit.set_defaults(func=cmd_trunk_audit)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
