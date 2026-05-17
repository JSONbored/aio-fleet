from __future__ import annotations

import json
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from aio_fleet.control_plane import publish_components
from aio_fleet.github_cli import github_cli_env
from aio_fleet.manifest import FleetManifest, RepoConfig
from aio_fleet.registry import compute_registry_tags, verify_registry_tags
from aio_fleet.release import (
    has_aio_unreleased_changes,
    has_semver_unreleased_changes,
    latest_aio_release_tag,
    latest_changelog_version,
    latest_semver_tag,
    next_aio_release_version,
    next_semver_release_version,
)


def release_plan_for_manifest(
    manifest: FleetManifest,
    *,
    include_registry: bool = False,
    catalog_sync: dict[str, bool] | None = None,
    redact_private: bool = False,
) -> list[dict[str, Any]]:
    return [
        (
            _private_release_plan(repo)
            if redact_private and repo.raw.get("public") is not True
            else release_plan_for_repo(
                repo,
                include_registry=include_registry,
                catalog_sync_needed=bool((catalog_sync or {}).get(repo.name)),
            )
        )
        for repo in manifest.repos.values()
    ]


def release_plan_for_repo(
    repo: RepoConfig,
    *,
    include_registry: bool = False,
    catalog_sync_needed: bool = False,
) -> dict[str, Any]:
    sha = _git_head(repo.path)
    warnings: list[str] = []
    blockers: list[str] = []
    registry_failures: list[str] = []
    registry_tags: dict[str, list[str]] = {"dockerhub": [], "ghcr": []}
    github_release = _latest_github_release(repo)

    if repo.publish_profile == "template":
        latest_tag = latest_semver_tag(repo.path)
        next_version = _safe_next_semver(repo)
        release_due = _safe_has_semver_changes(repo)
    else:
        latest_tag = _safe_latest_aio_tag(repo)
        next_version = _safe_next_aio(repo)
        release_due = _safe_has_aio_changes(repo)
    if not latest_tag and github_release.get("state") == "ok":
        latest_tag = str(github_release.get("tag", ""))
    target_commit = str(github_release.get("target_commitish", ""))
    if _looks_like_sha(target_commit) and sha:
        release_due = target_commit != sha

    changelog_version = _safe_changelog_version(repo)

    if include_registry and repo.publish_profile != "template":
        for component in publish_components(repo):
            tags = compute_registry_tags(repo, sha=sha, component=component)
            registry_tags["dockerhub"].extend(tags.dockerhub)
            registry_tags["ghcr"].extend(tags.ghcr)
            failures = verify_registry_tags(tags.all_tags)
            registry_failures.extend(f"{component}: {failure}" for failure in failures)
        if registry_failures:
            blockers.append("missing or unreachable registry tags")

    if catalog_sync_needed:
        warnings.append("catalog sync needed after source merge")

    if not latest_tag:
        warnings.append("no formal release tag found")
    if not changelog_version:
        warnings.append("latest changelog version unavailable")

    state = "current"
    if registry_failures:
        state = "publish-missing"
    elif catalog_sync_needed:
        state = "catalog-sync-needed"
    elif release_due:
        state = "release-due"
    elif not latest_tag or not changelog_version:
        state = "watch"

    return {
        "repo": repo.name,
        "profile": repo.publish_profile,
        "sha": sha,
        "latest_release_tag": latest_tag or "",
        "latest_changelog_version": changelog_version,
        "latest_github_release": github_release,
        "next_version": next_version,
        "release_due": bool(release_due),
        "catalog_sync_needed": catalog_sync_needed,
        "registry_state": "failed" if registry_failures else "ok",
        "registry_tags": registry_tags,
        "registry_failures": registry_failures,
        "state": state,
        "blockers": blockers,
        "warnings": warnings,
        "next_action": _next_release_action(repo, state, next_version),
    }


def _private_release_plan(repo: RepoConfig) -> dict[str, Any]:
    return {
        "repo": repo.name,
        "profile": "private-skipped",
        "sha": "",
        "latest_release_tag": "private-skipped",
        "latest_changelog_version": "private-skipped",
        "latest_github_release": {"state": "private-skipped"},
        "next_version": "",
        "release_due": False,
        "catalog_sync_needed": False,
        "registry_state": "private-skipped",
        "registry_tags": {"dockerhub": [], "ghcr": []},
        "registry_failures": [],
        "state": "private-skipped",
        "blockers": [],
        "warnings": ["private-skipped"],
        "next_action": "private-skipped",
    }


def _next_release_action(repo: RepoConfig, state: str, next_version: str) -> str:
    if state == "current":
        return "none"
    if state == "publish-missing":
        return f"python -m aio_fleet registry publish --repo {repo.name}"
    if state == "catalog-sync-needed":
        return f"python -m aio_fleet sync-catalog --repo {repo.name} --catalog-path ../awesome-unraid --dry-run"
    if state == "release-due" and next_version:
        return f"python -m aio_fleet release prepare --repo {repo.name} --dry-run"
    return f"python -m aio_fleet release status --repo {repo.name}"


def _safe_latest_aio_tag(repo: RepoConfig) -> str:
    try:
        return (
            latest_aio_release_tag(
                repo.path, repo.path / "Dockerfile", repo.path / "upstream.toml"
            )
            or ""
        )
    except (Exception, SystemExit):
        return ""


def _safe_next_aio(repo: RepoConfig) -> str:
    try:
        return next_aio_release_version(
            repo.path, repo.path / "Dockerfile", repo.path / "upstream.toml"
        )
    except (Exception, SystemExit):
        return ""


def _safe_has_aio_changes(repo: RepoConfig) -> bool:
    try:
        return has_aio_unreleased_changes(repo.path)
    except (Exception, SystemExit):
        return False


def _safe_next_semver(repo: RepoConfig) -> str:
    try:
        return next_semver_release_version(repo.path)
    except (Exception, SystemExit):
        return ""


def _safe_has_semver_changes(repo: RepoConfig) -> bool:
    try:
        return has_semver_unreleased_changes(repo.path)
    except (Exception, SystemExit):
        return False


def _safe_changelog_version(repo: RepoConfig) -> str:
    try:
        return latest_changelog_version(
            repo.path / "CHANGELOG.md", semver=repo.publish_profile == "template"
        )
    except (Exception, SystemExit):
        return ""


def _latest_github_release(repo: RepoConfig) -> dict[str, str]:
    result = subprocess.run(  # nosec B603 B607
        [
            "gh",
            "release",
            "view",
            "--repo",
            repo.github_repo,
            "--json",
            "tagName,publishedAt,targetCommitish,url",
        ],
        check=False,
        text=True,
        capture_output=True,
        env=github_cli_env(
            (
                "AIO_FLEET_DASHBOARD_TOKEN",
                "AIO_FLEET_UPSTREAM_TOKEN",
                "AIO_FLEET_CHECK_TOKEN",
                "APP_TOKEN",
                "GH_TOKEN",
                "GITHUB_TOKEN",
            )
        ),
    )
    if result.returncode != 0:
        return {"state": "unknown", "detail": (result.stderr or result.stdout).strip()}
    try:
        release = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {"state": "unknown", "detail": "invalid gh release JSON"}
    if not isinstance(release, dict) or not release:
        return {"state": "missing", "tag": "", "url": ""}
    return {
        "state": "ok",
        "tag": str(release.get("tagName", "")),
        "published_at": str(release.get("publishedAt", "")),
        "target_commitish": str(release.get("targetCommitish", "")),
        "url": str(release.get("url", "")),
    }


def _git_head(path: Path) -> str:
    result = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=False,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _looks_like_sha(value: str) -> bool:
    return len(value) == 40 and all(char in "0123456789abcdefABCDEF" for char in value)
