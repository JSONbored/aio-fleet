from __future__ import annotations

import argparse
import difflib
import fnmatch
import json
import os
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path

from aio_fleet.boilerplate import sync_boilerplate
from aio_fleet.catalog import sync_catalog_assets, unpublished_xml_targets
from aio_fleet.github_policy import load_policy, validate_github_policy
from aio_fleet.manifest import FleetManifest, ManifestError, RepoConfig, load_manifest
from aio_fleet.validators import (
    PINNED_REUSABLE_WORKFLOW,
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
from aio_fleet.workflows import (
    render_caller_workflow,
    rendered_workflows,
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


def _reusable_ref_from_caller(repo_path: Path) -> str:
    workflow = repo_path / ".github" / "workflows" / "build.yml"
    if not workflow.exists():
        raise ManifestError(f"caller workflow missing: {workflow}")
    match = PINNED_REUSABLE_WORKFLOW.search(workflow.read_text())
    if not match:
        raise ManifestError(f"{workflow} does not call aio-fleet at a pinned SHA")
    return match.group(1)


def _workflow_ref_for_repo(repo: RepoConfig) -> str:
    try:
        return _reusable_ref_from_caller(repo.path)
    except ManifestError:
        return _current_ref()


def cmd_doctor(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    failures: list[str] = []
    for name, repo in manifest.repos.items():
        if not repo.path.exists():
            failures.append(f"{name}: repo path missing: {repo.path}")
            continue
        for required in [
            "Dockerfile",
            "scripts/validate-template.py",
            "scripts/validate-derived-repo.sh",
        ]:
            if not (repo.path / required).exists():
                failures.append(f"{name}: missing {required}")
        for workflow in rendered_workflows(manifest, repo, "0" * 40):
            if not workflow.exists():
                failures.append(f"{name}: missing {workflow.relative_to(repo.path)}")
                continue
            workflow_text = workflow.read_text()
            if not PINNED_REUSABLE_WORKFLOW.search(workflow_text):
                failures.append(
                    f"{name}: {workflow.relative_to(repo.path)} does not call aio-fleet at a pinned SHA"
                )
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
    target_ref = args.ref
    catalog_failures = (
        catalog_repo_failures(manifest, catalog_path) if catalog_path else []
    )
    report: dict[str, object] = {
        "ref": target_ref or "caller-pins",
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

        workflow_drift = _workflow_drift(
            repo, manifest, target_ref or _workflow_ref_for_repo(repo)
        )
        boilerplate_drift = [
            str(change.target.relative_to(repo.path))
            for change in sync_boilerplate(
                repo,
                config_path=Path(args.boilerplate_config),
                profile=str(repo.get("boilerplate_profile", "aio")),
                dry_run=True,
            )
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
            "workflow_drift": workflow_drift,
            "boilerplate_drift": boilerplate_drift,
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
        "workflow_drift": sum(bool(item["workflow_drift"]) for item in repos),
        "boilerplate_drift": sum(bool(item["boilerplate_drift"]) for item in repos),
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


def _workflow_drift(repo: RepoConfig, manifest: FleetManifest, ref: str) -> list[str]:
    drift: list[str] = []
    for path, expected in rendered_workflows(manifest, repo, ref).items():
        if not path.exists():
            drift.append(str(path.relative_to(repo.path)))
            continue
        if path.read_text() != expected:
            drift.append(str(path.relative_to(repo.path)))
    return drift


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
            "workflow_drift",
            "boilerplate_drift",
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
            "workflow_drift",
            "boilerplate_drift",
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


def cmd_render_workflow(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repo = manifest.repo(args.repo)
    ref = args.ref or _current_ref()
    print(render_caller_workflow(manifest, repo, ref))
    return 0


def _sync_repo(
    repo: RepoConfig, manifest: FleetManifest, ref: str, dry_run: bool
) -> bool:
    changes = 0
    for path, rendered in rendered_workflows(manifest, repo, ref).items():
        current = path.read_text() if path.exists() else ""
        if current == rendered:
            continue
        changes += 1
        if dry_run:
            print(f"would update {repo.name}: {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered)
        print(f"updated {repo.name}: {path}")
    return changes > 0


def _git_commit_and_pr(
    repo: RepoConfig,
    *,
    branch: str,
    base: str,
    draft: bool,
    dry_run: bool,
) -> None:
    title = "ci(fleet): use shared AIO workflows"
    body = """## Summary
- Converts this repository to the shared AIO fleet workflows.

## What changed
- Replaces duplicated build, upstream-check, and release workflow logic with pinned aio-fleet reusable workflows
- Keeps repo-specific inputs in small local caller workflows

## Why
- Centralizes CI, publish gates, upstream monitoring, release preparation, Docker cache behavior, and catalog sync behavior across the AIO fleet

## Validation
- Generated from JSONbored/aio-fleet manifest
"""
    commands = [
        ["git", "checkout", "-B", branch],
        ["git", "add", ".github/workflows"],
        ["git", "commit", "-m", title],
        ["git", "push", "-u", "origin", branch],
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
            *(["--draft"] if draft else []),
        ],
    ]
    for command in commands:
        if dry_run:
            print(f"{repo.name}: would run {' '.join(command)}")
            continue
        result = _run(command, cwd=repo.path)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        if result.returncode != 0:
            raise RuntimeError(f"{repo.name}: command failed: {' '.join(command)}")


def cmd_sync_workflows(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repos = [manifest.repo(args.repo)] if args.repo else manifest.repos.values()
    changed = 0
    for repo in repos:
        ref = args.ref or _workflow_ref_for_repo(repo)
        did_change = _sync_repo(repo, manifest, ref, args.dry_run)
        changed += int(did_change)
        if did_change and args.create_pr:
            _git_commit_and_pr(
                repo,
                branch=args.branch,
                base=args.base,
                draft=args.draft,
                dry_run=args.dry_run,
            )
    print(f"workflow changes: {changed}")
    return 0


def cmd_verify_caller(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repo = _repo_for_identifier(manifest, args.repo)
    repo_path = Path(args.repo_path).resolve()
    ref = args.ref or _reusable_ref_from_caller(repo_path)
    repo_at_path = _repo_with_path(repo, repo_path)
    failures: list[str] = []

    for path, expected in rendered_workflows(manifest, repo_at_path, ref).items():
        relative = path.relative_to(repo_path)
        if not path.exists():
            failures.append(f"{relative}: missing generated caller workflow")
            continue
        current = path.read_text()
        if current == expected:
            continue
        failures.append(f"{relative}: out of date with aio-fleet manifest")
        if args.diff:
            failures.extend(
                difflib.unified_diff(
                    current.splitlines(),
                    expected.splitlines(),
                    fromfile=f"current/{relative}",
                    tofile=f"expected/{relative}",
                    lineterm="",
                )
            )

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"{repo.name} caller workflows match aio-fleet@{ref}")
    return 0


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
    result = _run(
        [
            "gh",
            "run",
            "list",
            "--repo",
            repo.github_repo,
            "--branch",
            "main",
            "--workflow",
            "build.yml",
            "--limit",
            "1",
            "--json",
            "status,conclusion,headSha,url",
        ],
        cwd=repo.path,
    )
    if result.returncode != 0:
        return {"state": "unknown", "detail": result.stderr.strip()}
    try:
        runs = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return {"state": "unknown", "detail": "unable to parse gh run output"}
    if not runs:
        return {"state": "missing", "detail": "no main build.yml run found"}
    run = runs[0]
    state = (
        "success"
        if run.get("status") == "completed" and run.get("conclusion") == "success"
        else str(run.get("conclusion") or run.get("status") or "unknown")
    )
    return {
        "state": state,
        "head_sha": str(run.get("headSha") or ""),
        "url": str(run.get("url") or ""),
    }


def _release_version(repo: RepoConfig) -> str:
    result = _run(
        ["python3", "scripts/release.py", "latest-changelog-version"], cwd=repo.path
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _image_status(repo: RepoConfig) -> str:
    docker = shutil.which("docker")
    if docker is None:
        return "unknown:no-docker"
    result = _run(
        [docker, "manifest", "inspect", f"{repo.image_name}:latest"],
        cwd=repo.path,
    )
    return "ok" if result.returncode == 0 else "unknown:latest-not-inspected"


def cmd_validate(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repos = manifest.repos.values() if args.all else [manifest.repo(args.repo)]
    failed = False
    for repo in repos:
        print(f"== {repo.name} ==")
        for cmd in (
            [_repo_python(repo.path), "scripts/validate-template.py", "--all"],
            [
                sys.executable,
                "-m",
                "aio_fleet.cli",
                "validate-derived",
                "--repo-path",
                ".",
            ],
        ):
            result = _run(cmd, cwd=repo.path)
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
            if result.returncode != 0:
                failed = True
                break
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


def cmd_sync_boilerplate(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repos = [manifest.repo(args.repo)] if args.repo else manifest.repos.values()
    changed = 0
    for repo in repos:
        profile = (
            str(repo.get("boilerplate_profile", "aio"))
            if args.profile == "auto"
            else args.profile
        )
        changes = sync_boilerplate(
            repo,
            config_path=Path(args.config),
            profile=profile,
            dry_run=args.dry_run,
        )
        changed += len(changes)
        for change in changes:
            relative = change.target.relative_to(repo.path)
            prefix = "would " if args.dry_run else ""
            print(f"{repo.name}: {prefix}{change.action} {relative}")
        if changes and args.create_pr:
            _boilerplate_commit_and_pr(
                repo,
                branch=args.branch,
                base=args.base,
                draft=args.draft,
                paths=[change.target for change in changes],
                dry_run=args.dry_run,
            )
    print(f"boilerplate changes: {changed}")
    return 0


def _boilerplate_commit_and_pr(
    repo: RepoConfig,
    *,
    branch: str,
    base: str,
    draft: bool,
    paths: list[Path],
    dry_run: bool,
) -> None:
    title = "chore(fleet): sync shared repository boilerplate"
    body = """## Summary
- Syncs shared AIO repository boilerplate from `aio-fleet`.

## What changed
- Updates common repository support files such as issue templates, funding metadata, or support docs
- Preserves app-specific files through explicit `aio-fleet` exclusions and create-only rules

## Why
- Reduces fleet drift without moving runtime or generated-template logic out of the app repo

## Validation
- Generated by `aio-fleet sync-boilerplate`
"""
    relative_paths = [str(path.relative_to(repo.path)) for path in paths]
    commands = [
        ["git", "checkout", "-B", branch],
        ["git", "add", *relative_paths],
        ["git", "commit", "-m", title],
        ["git", "push", "-u", "origin", branch],
    ]
    for command in commands:
        if dry_run:
            print(f"{repo.name}: would run {' '.join(command)}")
            continue
        result = _run(command, cwd=repo.path)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        if result.returncode != 0:
            raise RuntimeError(f"{repo.name}: command failed: {' '.join(command)}")

    if dry_run:
        print(f"{repo.name}: would open or update PR {branch} -> {base}")
        return

    existing = _run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo.github_repo,
            "--head",
            branch,
            "--base",
            base,
            "--json",
            "number",
            "--jq",
            ".[0].number // empty",
        ],
        cwd=repo.path,
    )
    number = existing.stdout.strip()
    if number:
        result = _run(
            [
                "gh",
                "pr",
                "edit",
                number,
                "--repo",
                repo.github_repo,
                "--title",
                title,
                "--body",
                body,
            ],
            cwd=repo.path,
        )
    else:
        result = _run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                repo.github_repo,
                "--base",
                base,
                "--head",
                branch,
                "--title",
                title,
                "--body",
                body,
                *(["--draft"] if draft else []),
            ],
            cwd=repo.path,
        )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode != 0:
        raise RuntimeError(f"{repo.name}: boilerplate PR command failed")


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
        patterns = set(
            str(item)
            for item in policy.get("defaults", {})
            .get("actions", {})
            .get("patterns_allowed", [])
        )
        if "JSONbored/aio-fleet/.github/workflows/aio-*.yml@*" not in patterns:
            failures.append(
                "github policy missing reusable workflow wildcard allowlist"
            )
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
    if args.format == "json":
        print(
            json.dumps({"repo": args.repo, "manifest_entry": manifest_entry}, indent=2)
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
    print(f"- `python -m aio_fleet render-workflow {args.repo} --ref <aio-fleet-sha>`")
    print(
        f"- `python -m aio_fleet sync-workflows --repo {args.repo} --ref <aio-fleet-sha>`"
    )
    print(
        f"- `python -m aio_fleet validate-repo --repo {args.repo} --repo-path ../{args.repo}`"
    )
    print(
        f"- `python -m aio_fleet sync-catalog --repo {args.repo} --catalog-path ../awesome-unraid --dry-run`"
    )
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
        Path(__file__).resolve().parents[2]
        / "boilerplate"
        / "aio"
        / "docs"
        / "support-thread-template.md"
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
    debt.add_argument("--boilerplate-config", default="boilerplate.yml")
    debt.add_argument("--ref")
    debt.add_argument("--trunk", action="store_true")
    debt.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    debt.set_defaults(func=cmd_debt_report)

    render = sub.add_parser("render-workflow")
    render.add_argument("repo")
    render.add_argument("--ref")
    render.set_defaults(func=cmd_render_workflow)

    sync = sub.add_parser("sync-workflows")
    sync.add_argument("--repo")
    sync.add_argument("--ref")
    sync.add_argument("--branch", default="codex/aio-fleet-workflows")
    sync.add_argument("--base", default="main")
    sync.add_argument("--create-pr", action="store_true")
    sync.add_argument("--draft", action="store_true")
    sync.add_argument("--dry-run", action="store_true")
    sync.set_defaults(func=cmd_sync_workflows)

    boilerplate = sub.add_parser("sync-boilerplate")
    boilerplate.add_argument("--repo")
    boilerplate.add_argument("--profile", default="auto")
    boilerplate.add_argument("--config", default="boilerplate.yml")
    boilerplate.add_argument("--create-pr", action="store_true")
    boilerplate.add_argument("--branch", default="codex/aio-fleet-boilerplate")
    boilerplate.add_argument("--base", default="main")
    boilerplate.add_argument("--draft", action="store_true")
    boilerplate.add_argument("--dry-run", action="store_true")
    boilerplate.set_defaults(func=cmd_sync_boilerplate)

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

    verify = sub.add_parser("verify-caller")
    verify.add_argument("--repo", required=True)
    verify.add_argument("--repo-path", default=".")
    verify.add_argument("--ref")
    verify.add_argument("--diff", action="store_true")
    verify.set_defaults(func=cmd_verify_caller)

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
