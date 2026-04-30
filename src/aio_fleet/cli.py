from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from pathlib import Path

from aio_fleet.manifest import FleetManifest, ManifestError, RepoConfig, load_manifest
from aio_fleet.workflows import (
    rendered_workflows,
    render_caller_workflow,
    workflow_path_for,
)

PINNED_REUSABLE_WORKFLOW = re.compile(
    r"uses:\s+JSONbored/aio-fleet/\.github/workflows/aio-[a-z-]+\.yml@([0-9a-f]{40})"
)


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=False, text=True, capture_output=True)


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
        for required in ["Dockerfile", "scripts/validate-template.py", "scripts/validate-derived-repo.sh"]:
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
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"fleet manifest ok: {len(manifest.repos)} repos")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    for name, repo in manifest.repos.items():
        branch = _run(["git", "branch", "--show-current"], cwd=repo.path)
        status = _run(["git", "status", "--short"], cwd=repo.path)
        dirty = "dirty" if status.stdout.strip() else "clean"
        pr_state = ""
        branch_name = branch.stdout.strip()
        if args.github and branch_name:
            pr = _run(
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
                        ".[0] // null | if . == null then \"no-pr\" "
                        "else \"#\\(.number) \\(.url) draft=\\(.isDraft) checks=\\(.statusCheckRollup | length)\" end"
                    ),
                ],
                cwd=repo.path,
            )
            pr_state = f" {pr.stdout.strip() or 'pr-unknown'}"
        print(f"{name:22} {branch_name or '-':36} {dirty}{pr_state}")
    return 0


def cmd_render_workflow(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repo = manifest.repo(args.repo)
    ref = args.ref or _current_ref()
    print(render_caller_workflow(manifest, repo, ref))
    return 0


def _sync_repo(repo: RepoConfig, manifest: FleetManifest, ref: str, dry_run: bool) -> bool:
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


def cmd_validate(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    repos = manifest.repos.values() if args.all else [manifest.repo(args.repo)]
    failed = False
    for repo in repos:
        print(f"== {repo.name} ==")
        for cmd in (
            [sys.executable, "scripts/validate-template.py", "--all"],
            ["bash", "scripts/validate-derived-repo.sh", "."],
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aio-fleet")
    parser.add_argument("--manifest", default="fleet.yml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    status = sub.add_parser("status")
    status.add_argument("--github", action="store_true")
    status.set_defaults(func=cmd_status)

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

    verify = sub.add_parser("verify-caller")
    verify.add_argument("--repo", required=True)
    verify.add_argument("--repo-path", default=".")
    verify.add_argument("--ref")
    verify.add_argument("--diff", action="store_true")
    verify.set_defaults(func=cmd_verify_caller)

    validate = sub.add_parser("validate")
    validate.add_argument("--all", action="store_true")
    validate.add_argument("--repo")
    validate.set_defaults(func=cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
