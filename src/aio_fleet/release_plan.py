from __future__ import annotations

import fnmatch
import json
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from aio_fleet.changelog import component_config
from aio_fleet.cleanup import RETIRED_SHARED_PATHS
from aio_fleet.control_plane import publish_components
from aio_fleet.github_cli import github_cli_env
from aio_fleet.manifest import FleetManifest, RepoConfig
from aio_fleet.registry import (
    component_registry_release_tag,
    compute_registry_tags,
    registry_sha_tag_required,
    verify_registry_tags,
)
from aio_fleet.release import (
    has_aio_unreleased_changes,
    has_semver_unreleased_changes,
    latest_aio_release_tag,
    latest_changelog_version,
    latest_component_changelog_version,
    latest_component_release_tag,
    latest_semver_tag,
    next_aio_release_version,
    next_semver_release_version,
    read_upstream_version,
)
from aio_fleet.signing import generated_pr_signature_blockers


def release_plan_for_manifest(
    manifest: FleetManifest,
    *,
    include_registry: bool = False,
    catalog_sync: dict[str, bool] | None = None,
    redact_private: bool = False,
    registry_verify_attempts: int = 8,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for repo in manifest.repos.values():
        if redact_private and repo.raw.get("public") is not True:
            rows.append(_private_release_plan(repo))
            continue
        rows.extend(
            release_plan_rows_for_repo(
                repo,
                include_registry=include_registry,
                catalog_sync_needed=bool((catalog_sync or {}).get(repo.name)),
                registry_verify_attempts=registry_verify_attempts,
            )
        )
    return rows


def release_plan_rows_for_repo(
    repo: RepoConfig,
    *,
    include_registry: bool = False,
    catalog_sync_needed: bool = False,
    registry_verify_attempts: int = 8,
) -> list[dict[str, Any]]:
    if repo.publish_profile == "template":
        return [
            release_plan_for_repo(
                repo,
                include_registry=include_registry,
                catalog_sync_needed=catalog_sync_needed,
                component="template",
                registry_verify_attempts=registry_verify_attempts,
            )
        ]
    return [
        release_plan_for_repo(
            repo,
            include_registry=include_registry,
            catalog_sync_needed=catalog_sync_needed,
            component=component,
            registry_verify_attempts=registry_verify_attempts,
        )
        for component in publish_components(repo)
    ]


def release_plan_for_repo(
    repo: RepoConfig,
    *,
    include_registry: bool = False,
    catalog_sync_needed: bool = False,
    component: str = "aio",
    registry_verify_attempts: int = 8,
) -> dict[str, Any]:
    sha = _git_head(repo.path)
    warnings: list[str] = []
    blockers: list[str] = []
    registry_failures: list[str] = []
    registry_tags: dict[str, list[str]] = {"dockerhub": [], "ghcr": []}
    config = component_config(repo, component)
    registry_only = str(config.get("release_policy", "")).strip() == "registry_only"
    github_release_required = _component_uses_github_release(
        config, registry_only=registry_only
    )
    registry_only_component_changes = False
    ignored_release_changes = False
    release_history_incomplete = False

    if repo.publish_profile == "template":
        latest_tag = latest_semver_tag(repo.path)
        next_version = _safe_next_semver(repo)
        release_due = _safe_has_semver_changes(repo)
    elif component != "aio":
        latest_tag = (
            _component_release_tag(repo, component)
            if registry_only
            else _safe_latest_component_tag(repo, component)
        )
        ignored_release_changes = _only_ignored_release_changes(repo, latest_tag)
        next_version = (
            _safe_next_component(repo, component)
            if github_release_required
            else latest_tag
        )
        registry_only_component_changes = registry_only
        release_due = (
            _has_registry_only_component_changes(repo, component, latest_tag)
            if registry_only
            else _safe_has_component_changes(repo, component)
        )
        if release_due and _only_ignored_or_other_component_changes(
            repo, component, latest_tag
        ):
            release_due = False
    else:
        latest_tag = _safe_latest_aio_tag(repo)
        ignored_release_changes = _only_ignored_release_changes(repo, latest_tag)
        next_version = _safe_next_aio(repo)
        registry_only_component_changes = _only_registry_only_component_changes(repo)
        release_due = _safe_has_aio_changes(repo)
        if release_due and _only_ignored_or_other_component_changes(
            repo, component, latest_tag
        ):
            release_due = False
    if repo.publish_profile != "template" and release_due and ignored_release_changes:
        release_due = False

    github_release = (
        _latest_github_release(repo, tag=latest_tag)
        if github_release_required
        else {
            "state": "not-applicable",
            "detail": "registry-only component without GitHub release history",
        }
    )
    if (
        not latest_tag
        and github_release_required
        and github_release.get("state") == "ok"
    ):
        latest_tag = str(github_release.get("tag", ""))
    target_commit = str(github_release.get("target_commitish", ""))
    if (
        github_release_required
        and _github_release_matches_component(github_release, latest_tag)
        and _looks_like_sha(target_commit)
        and sha
        and not registry_only_component_changes
        and not ignored_release_changes
    ):
        release_history_warning = _release_history_warning(
            repo.path, latest_tag=latest_tag, target_commit=target_commit
        )
        if release_history_warning:
            release_history_incomplete = True
            release_due = False
            warnings.append(release_history_warning)
        elif not _only_ignored_or_other_component_changes(repo, component, latest_tag):
            release_due = target_commit != sha
    if (
        github_release_required
        and latest_tag
        and _github_release_missing(github_release)
    ):
        release_due = True

    changelog_required = _component_requires_changelog(
        repo, config, registry_only=registry_only
    )
    changelog_version = (
        _safe_changelog_version(repo, component=component)
        if changelog_required
        else "registry-only"
    )

    if include_registry and repo.publish_profile != "template":
        tags = compute_registry_tags(
            repo,
            sha=sha,
            component=component,
            include_sha_tag=registry_sha_tag_required(
                repo, sha=sha, component=component
            ),
        )
        registry_tags["dockerhub"].extend(tags.dockerhub)
        registry_tags["ghcr"].extend(tags.ghcr)
        registry_failures.extend(
            verify_registry_tags(
                tags.all_tags, dockerhub_attempts=registry_verify_attempts
            )
        )
        if registry_failures:
            blockers.append("missing or unreachable registry tags")

    if catalog_sync_needed:
        warnings.append("catalog sync needed after source merge")

    signing_blockers = (
        []
        if repo.raw.get("public") is not True
        else generated_pr_signature_blockers(repo.github_repo)
    )
    blockers.extend(signing_blockers)

    if not latest_tag:
        warnings.append(
            "no formal release tag found"
            if github_release_required
            else "registry package tag unavailable"
        )
    if changelog_required and not changelog_version:
        warnings.append("latest changelog version unavailable")

    state = "current"
    if signing_blockers:
        state = "blocked"
    elif registry_failures:
        state = "publish-missing"
    elif catalog_sync_needed:
        state = "catalog-sync-needed"
    elif release_due:
        state = "release-due"
    elif release_history_incomplete:
        state = "watch"
    elif not latest_tag or (changelog_required and not changelog_version):
        state = "watch"

    return {
        "repo": repo.name,
        "component": component,
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
        "next_action": _next_release_action(
            repo, state, next_version, component=component, sha=sha
        ),
        "operator_commands": _operator_commands(repo, component=component, sha=sha),
    }


def _private_release_plan(repo: RepoConfig) -> dict[str, Any]:
    return {
        "repo": repo.name,
        "component": "private",
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
        "operator_commands": {},
    }


def _next_release_action(
    repo: RepoConfig,
    state: str,
    next_version: str,
    *,
    component: str = "aio",
    sha: str = "",
) -> str:
    if state == "blocked":
        return f"python -m aio_fleet signing doctor --repo {repo.name} --format json"
    if state == "current":
        return "none"
    if state == "publish-missing":
        return release_transaction_command(repo, component=component, sha=sha)
    if state == "catalog-sync-needed":
        return f"python -m aio_fleet sync-catalog --repo {repo.name} --catalog-path ../awesome-unraid --dry-run"
    if state == "release-due" and next_version:
        return release_transaction_command(repo, component=component, sha=sha)
    return (
        f"python -m aio_fleet release status --repo {repo.name} --component {component}"
    )


def _operator_commands(
    repo: RepoConfig, *, component: str = "aio", sha: str = ""
) -> dict[str, str]:
    if repo.publish_profile == "template":
        return {}
    label_sha = sha if sha else "<sha>"
    config = component_config(repo, component)
    registry_only = str(config.get("release_policy", "")).strip() == "registry_only"
    commands = {
        "registry_verify": f"python -m aio_fleet registry verify --repo {repo.name} --component {component} --sha {label_sha} --verbose",
        "registry_publish": f"python -m aio_fleet registry publish --repo {repo.name} --component {component}",
        "control_check_publish": control_check_publish_command(
            repo, component=component, sha=sha
        ),
        "release_transaction": release_transaction_command(
            repo, component=component, sha=sha
        ),
    }
    if _component_uses_github_release(config, registry_only=registry_only):
        commands["release_publish"] = (
            f"python -m aio_fleet release publish --repo {repo.name} --component {component}"
        )
    return commands


def _component_uses_github_release(
    config: dict[str, object], *, registry_only: bool
) -> bool:
    if not registry_only:
        return True
    return str(config.get("release_history", "")).strip() == "github_prerelease"


def _component_requires_changelog(
    repo: RepoConfig, config: dict[str, object], *, registry_only: bool
) -> bool:
    if repo.publish_profile == "template":
        return True
    return _component_uses_github_release(config, registry_only=registry_only)


def _github_release_matches_component(
    github_release: dict[str, str], latest_tag: str
) -> bool:
    if github_release.get("state") != "ok":
        return False
    return not latest_tag or str(github_release.get("tag", "")) == latest_tag


def _github_release_missing(github_release: dict[str, str]) -> bool:
    if github_release.get("state") == "missing":
        return True
    detail = str(github_release.get("detail", "") or "").lower()
    return github_release.get("state") == "unknown" and "not found" in detail


def control_check_publish_command(
    repo: RepoConfig, *, component: str = "aio", sha: str = ""
) -> str:
    label_sha = sha if sha else "<sha>"
    return (
        f"python -m aio_fleet control-check --repo {repo.name} --sha {label_sha} "
        f"--event push --publish --publish-component {component}"
    )


def release_transaction_command(
    repo: RepoConfig, *, component: str = "aio", sha: str = ""
) -> str:
    label_sha = sha if sha else "<sha>"
    return (
        f"python -m aio_fleet release transaction --repo {repo.name} "
        f"--component {component} --sha {label_sha} --dry-run"
    )


def _safe_latest_aio_tag(repo: RepoConfig) -> str:
    try:
        if repo.publish_profile == "changelog-version":
            return latest_component_release_tag(repo.path) or ""
        config = component_config(repo, "aio")
        return (
            latest_aio_release_tag(
                repo.path,
                repo.path / str(config.get("dockerfile", "Dockerfile")),
                repo.path / str(config.get("upstream_config", "upstream.toml")),
                suffix=str(config.get("release_suffix", "aio")),
                version_key=str(config.get("upstream_version_key", "UPSTREAM_VERSION")),
                tag_prefix=str(config.get("release_tag_prefix", "") or ""),
            )
            or ""
        )
    except (Exception, SystemExit):
        return ""


def _component_release_tag(repo: RepoConfig, component: str) -> str:
    try:
        release_tag = component_registry_release_tag(repo, component)
        prefix = str(component_config(repo, component).get("release_tag_prefix", ""))
        return f"{prefix}{release_tag}" if release_tag and prefix else release_tag
    except (Exception, SystemExit):
        return ""


def _safe_latest_component_tag(repo: RepoConfig, component: str) -> str:
    try:
        config = component_config(repo, component)
        return (
            latest_aio_release_tag(
                repo.path,
                repo.path / str(config.get("dockerfile", "Dockerfile")),
                repo.path / str(config.get("upstream_config", "upstream.toml")),
                suffix=str(config.get("release_suffix", "aio")),
                version_key=str(config.get("upstream_version_key", "UPSTREAM_VERSION")),
                tag_prefix=str(config.get("release_tag_prefix", "") or ""),
            )
            or ""
        )
    except (Exception, SystemExit):
        return ""


def _safe_next_component(repo: RepoConfig, component: str) -> str:
    try:
        config = component_config(repo, component)
        return next_aio_release_version(
            repo.path,
            repo.path / str(config.get("dockerfile", "Dockerfile")),
            repo.path / str(config.get("upstream_config", "upstream.toml")),
            suffix=str(config.get("release_suffix", "aio")),
            version_key=str(config.get("upstream_version_key", "UPSTREAM_VERSION")),
            tag_prefix=str(config.get("release_tag_prefix", "") or ""),
        )
    except (Exception, SystemExit):
        return ""


def _safe_has_component_changes(repo: RepoConfig, component: str) -> bool:
    try:
        config = component_config(repo, component)
        suffix = str(config.get("release_suffix", "aio"))
        return has_aio_unreleased_changes(repo.path, suffix=suffix)
    except (Exception, SystemExit):
        return False


def _safe_next_aio(repo: RepoConfig) -> str:
    try:
        config = component_config(repo, "aio")
        return next_aio_release_version(
            repo.path,
            repo.path / str(config.get("dockerfile", "Dockerfile")),
            repo.path / str(config.get("upstream_config", "upstream.toml")),
            suffix=str(config.get("release_suffix", "aio")),
            version_key=str(config.get("upstream_version_key", "UPSTREAM_VERSION")),
            tag_prefix=str(config.get("release_tag_prefix", "") or ""),
        )
    except (Exception, SystemExit):
        return ""


def _safe_has_aio_changes(repo: RepoConfig) -> bool:
    try:
        if _only_ignored_release_changes(repo):
            return False
        if _only_registry_only_component_changes(repo):
            return False
        config = component_config(repo, "aio")
        return has_aio_unreleased_changes(
            repo.path, suffix=str(config.get("release_suffix", "aio"))
        )
    except (Exception, SystemExit):
        return False


def _only_ignored_release_changes(repo: RepoConfig, latest_tag: str = "") -> bool:
    patterns = set(repo.list_value("non_release_paths"))
    patterns.update(RETIRED_SHARED_PATHS)
    latest_tag = latest_tag or _safe_latest_aio_tag(repo)
    if not patterns or not latest_tag:
        return False
    changed_paths = _changed_paths_since(repo.path, latest_tag)
    if not changed_paths:
        return False
    return all(_matches_release_pattern(path, patterns) for path in changed_paths)


def _only_registry_only_component_changes(repo: RepoConfig) -> bool:
    patterns = _registry_only_component_patterns(repo)
    if not patterns:
        return False
    latest_tag = _safe_latest_aio_tag(repo)
    if not latest_tag:
        return False
    changed_paths = _changed_paths_since(repo.path, latest_tag)
    if not changed_paths:
        return False
    return all(_matches_release_pattern(path, patterns) for path in changed_paths)


def _only_other_component_changes(
    repo: RepoConfig, component: str, latest_tag: str
) -> bool:
    components = repo.raw.get("components")
    if not isinstance(components, dict) or not latest_tag:
        return False
    patterns: set[str] = set()
    for name in components:
        if str(name) == component:
            continue
        patterns.update(_component_release_patterns(repo, str(name)))
    if not patterns:
        return False
    changed_paths = _changed_paths_since(repo.path, latest_tag)
    if not changed_paths:
        return False
    return all(_matches_release_pattern(path, patterns) for path in changed_paths)


def _only_ignored_or_other_component_changes(
    repo: RepoConfig, component: str, latest_tag: str
) -> bool:
    components = repo.raw.get("components")
    if not isinstance(components, dict) or not latest_tag:
        return _only_ignored_release_changes(repo, latest_tag)
    patterns: set[str] = set(repo.list_value("non_release_paths"))
    patterns.update(RETIRED_SHARED_PATHS)
    for name in components:
        if str(name) == component:
            continue
        patterns.update(_component_release_patterns(repo, str(name)))
    if not patterns:
        return False
    changed_paths = _changed_paths_since(repo.path, latest_tag)
    if not changed_paths:
        return False
    return all(_matches_release_pattern(path, patterns) for path in changed_paths)


def _has_registry_only_component_changes(
    repo: RepoConfig, component: str, latest_tag: str
) -> bool:
    patterns = _registry_only_component_patterns(repo, component=component)
    if not patterns or not latest_tag:
        return False
    changed_paths = _changed_paths_since(repo.path, latest_tag)
    if not changed_paths:
        return False
    return any(_matches_release_pattern(path, patterns) for path in changed_paths)


def _registry_only_component_patterns(
    repo: RepoConfig, *, component: str | None = None
) -> set[str]:
    patterns: set[str] = set()
    components = repo.raw.get("components")
    if not isinstance(components, dict):
        return patterns
    for name, config in components.items():
        if component is not None and name != component:
            continue
        if not isinstance(config, dict):
            continue
        if str(config.get("release_policy", "")).strip() != "registry_only":
            continue
        patterns.add(".aio-fleet.yml")
        patterns.update(_component_release_patterns(repo, str(name)))
    return {pattern for pattern in patterns if pattern}


def _component_release_patterns(repo: RepoConfig, component: str) -> set[str]:
    config = component_config(repo, component)
    patterns: set[str] = set()
    for key, default in (
        ("dockerfile", "Dockerfile"),
        ("upstream_config", "upstream.toml"),
        ("release_changelog", "CHANGELOG.md"),
    ):
        value = str(config.get(key, default)).strip()
        if value:
            patterns.add(value)
    patterns.update(_string_list(config.get("xml_paths", [])))
    patterns.update(_string_list(config.get("publish_paths", [])))
    for monitor in repo.raw.get("upstream_monitor", []):
        if (
            isinstance(monitor, dict)
            and str(monitor.get("component", "aio")) == component
            and monitor.get("dockerfile")
        ):
            patterns.add(str(monitor["dockerfile"]))
    return {pattern for pattern in patterns if pattern}


def _string_list(value: object) -> set[str]:
    if isinstance(value, str):
        return {value} if value.strip() else set()
    if isinstance(value, list):
        return {str(item) for item in value if str(item).strip()}
    return set()


def _matches_release_pattern(path: str, patterns: set[str]) -> bool:
    for pattern in patterns:
        normalized = pattern.rstrip("/")
        if path == normalized or path.startswith(f"{normalized}/"):
            return True
        if fnmatch.fnmatch(path, pattern):
            return True
    return False


def _changed_paths_since(repo_path: Path, ref: str) -> list[str]:
    verify = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{ref}"],
        cwd=repo_path,
        check=False,
        text=True,
        capture_output=True,
    )
    if verify.returncode != 0:
        return []
    result = subprocess.run(  # nosec B603 B607
        ["git", "diff", "--name-only", f"{ref}..HEAD", "--"],
        cwd=repo_path,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _release_history_warning(
    repo_path: Path, *, latest_tag: str, target_commit: str
) -> str:
    if _git_is_shallow(repo_path):
        return "release history incomplete; fetch full history and tags before trusting release due"
    if latest_tag and not _git_has_tag(repo_path, latest_tag):
        return f"release tag {latest_tag} is missing locally; fetch tags before trusting release due"
    if (
        target_commit
        and _looks_like_sha(target_commit)
        and not _git_has_commit(repo_path, target_commit)
    ):
        return f"release target {target_commit} is missing locally; fetch full history before trusting release due"
    return ""


def _git_is_shallow(repo_path: Path) -> bool:
    result = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=repo_path,
        check=False,
        text=True,
        capture_output=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _git_has_tag(repo_path: Path, tag: str) -> bool:
    result = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"],
        cwd=repo_path,
        check=False,
        text=True,
        capture_output=True,
    )
    return result.returncode == 0


def _git_has_commit(repo_path: Path, sha: str) -> bool:
    result = subprocess.run(  # nosec B603 B607
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=repo_path,
        check=False,
        text=True,
        capture_output=True,
    )
    return result.returncode == 0


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


def _safe_changelog_version(repo: RepoConfig, *, component: str = "aio") -> str:
    try:
        config = component_config(repo, component)
        changelog = repo.path / str(config.get("release_changelog", "CHANGELOG.md"))
        if repo.publish_profile == "template":
            return latest_changelog_version(changelog, semver=True)
        if repo.publish_profile == "changelog-version":
            return latest_changelog_version(changelog)
        upstream_version = read_upstream_version(
            repo.path / str(config.get("dockerfile", "Dockerfile")),
            repo.path / str(config.get("upstream_config", "upstream.toml")),
            version_key=str(config.get("upstream_version_key", "UPSTREAM_VERSION")),
        )
        return latest_component_changelog_version(
            changelog,
            upstream_version=upstream_version,
            suffix=str(config.get("release_suffix", "aio")),
        )
    except (Exception, SystemExit):
        return ""


def _latest_github_release(repo: RepoConfig, *, tag: str = "") -> dict[str, str]:
    command = ["gh", "release", "view"]
    if tag:
        command.append(tag)
    command.extend(
        [
            "--repo",
            repo.github_repo,
            "--json",
            "tagName,publishedAt,targetCommitish,url",
        ]
    )
    result = subprocess.run(  # nosec B603 B607
        command,
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
