from __future__ import annotations

import argparse
import difflib
import fnmatch
import json
import os
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
    catalog_repo_failures,
    derived_repo_failures,
    pinned_action_failures,
    repo_policy_failures,
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
    current_ref = args.ref or _current_ref()
    catalog_failures = (
        catalog_repo_failures(manifest, catalog_path) if catalog_path else []
    )
    report: dict[str, object] = {
        "ref": current_ref,
        "catalog_path": str(catalog_path) if catalog_path else None,
        "repos": [],
    }

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

        workflow_drift = _workflow_drift(repo, manifest, current_ref)
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
        if args.github:
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
            f"{item['repo']}: {status} publish={item['publish']} trunk={item['trunk']} open_prs={item['open_prs']}"  # type: ignore[index]
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
    ref = args.ref or _current_ref()
    repos = [manifest.repo(args.repo)] if args.repo else manifest.repos.values()
    changed = 0
    for repo in repos:
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

    repo = sub.add_parser("validate-repo")
    repo.add_argument("--repo", required=True)
    repo.add_argument("--repo-path", default=".")
    repo.set_defaults(func=cmd_validate_repo)

    catalog = sub.add_parser("validate-catalog")
    catalog.add_argument("--catalog-path", required=True)
    catalog.set_defaults(func=cmd_validate_catalog)

    github = sub.add_parser("validate-github")
    github.add_argument("--policy", default="infra/github/github-policy.yml")
    github.add_argument("--repo", action="append")
    github.add_argument("--check-secrets", action="store_true")
    github.set_defaults(func=cmd_validate_github)

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
