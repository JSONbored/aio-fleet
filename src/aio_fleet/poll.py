from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404
from dataclasses import dataclass
from fnmatch import fnmatch

from aio_fleet.manifest import FleetManifest, RepoConfig


@dataclass(frozen=True)
class PollTarget:
    repo: RepoConfig
    sha: str
    event: str
    source: str
    checkout_submodules: bool = False
    publish: bool = False


def poll_targets(
    manifest: FleetManifest,
    *,
    include_prs: bool = True,
    include_main: bool = True,
) -> list[PollTarget]:
    targets: list[PollTarget] = []
    for repo in manifest.repos.values():
        if include_prs:
            for pull_request in _open_pull_requests(repo):
                if not _same_repository_pull_request(repo, pull_request):
                    continue
                sha = str(pull_request.get("headRefOid") or "")
                number = str(pull_request.get("number") or "")
                if sha:
                    targets.append(
                        PollTarget(
                            repo=repo,
                            sha=sha,
                            event="pull_request",
                            source=f"pr:{number}",
                            checkout_submodules=bool(
                                repo.raw.get("checkout_submodules")
                            ),
                            publish=False,
                        )
                    )
        if include_main:
            sha = _main_sha(repo)
            if sha:
                targets.append(
                    PollTarget(
                        repo=repo,
                        sha=sha,
                        event="push",
                        source="main",
                        checkout_submodules=bool(repo.raw.get("checkout_submodules")),
                        publish=publish_required(repo, sha=sha, event="push"),
                    )
                )
    return targets


def publish_required(repo: RepoConfig, *, sha: str, event: str) -> bool:
    if event != "push" or repo.publish_profile == "template":
        return False
    changed_paths = _commit_changed_paths(repo, sha)
    if changed_paths is None:
        return True
    return any(_is_publish_related_path(repo, path) for path in changed_paths)


def _commit_changed_paths(repo: RepoConfig, sha: str) -> list[str] | None:
    result = _gh(
        [
            "api",
            f"repos/{repo.github_repo}/commits/{sha}",
            "--jq",
            ".files[].filename",
        ]
    )
    if result.returncode != 0:
        return None
    paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return paths or None


def _is_publish_related_path(repo: RepoConfig, path: str) -> bool:
    return any(fnmatch(path, pattern) for pattern in _publish_related_patterns(repo))


def _publish_related_patterns(repo: RepoConfig) -> set[str]:
    patterns = {
        ".aio-fleet.yml",
        "CHANGELOG.md",
        "Containerfile",
        "Dockerfile",
        "rootfs/**",
        "upstream.toml",
    }
    for key in ("extra_publish_paths", "upstream_commit_paths", "xml_paths"):
        patterns.update(repo.list_value(key))
    for monitor in repo.raw.get("upstream_monitor", []):
        if isinstance(monitor, dict) and monitor.get("dockerfile"):
            patterns.add(str(monitor["dockerfile"]))
    components = repo.raw.get("components")
    if isinstance(components, dict):
        for config in components.values():
            if not isinstance(config, dict):
                continue
            if config.get("dockerfile"):
                patterns.add(str(config["dockerfile"]))
            for xml_path in config.get("xml_paths", []):
                patterns.add(str(xml_path))
    return {pattern for pattern in patterns if pattern}


def _open_pull_requests(repo: RepoConfig) -> list[dict[str, object]]:
    result = _gh(
        [
            "pr",
            "list",
            "--repo",
            repo.github_repo,
            "--state",
            "open",
            "--json",
            "number,headRefOid,headRepository,headRepositoryOwner,isCrossRepository",
        ]
    )
    if result.returncode != 0:
        return []
    return json.loads(result.stdout or "[]")


def _same_repository_pull_request(
    repo: RepoConfig, pull_request: dict[str, object]
) -> bool:
    if pull_request.get("isCrossRepository") is True:
        return False
    if pull_request.get("isCrossRepository") is False:
        return True

    expected = repo.github_repo.casefold()
    head_repository = pull_request.get("headRepository")
    if isinstance(head_repository, dict):
        name_with_owner = _string_value(head_repository.get("nameWithOwner"))
        if name_with_owner:
            return name_with_owner.casefold() == expected

        name = _string_value(head_repository.get("name"))
        owner = _head_repository_owner(pull_request)
        if name and owner:
            return f"{owner}/{name}".casefold() == expected

    return False


def _head_repository_owner(pull_request: dict[str, object]) -> str:
    owner = pull_request.get("headRepositoryOwner")
    if isinstance(owner, dict):
        return _string_value(owner.get("login"))
    return _string_value(owner)


def _string_value(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _main_sha(repo: RepoConfig) -> str:
    result = _gh(["api", f"repos/{repo.github_repo}/commits/main", "--jq", ".sha"])
    return result.stdout.strip() if result.returncode == 0 else ""


def _gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    gh = shutil.which("gh")
    if gh is None:
        return subprocess.CompletedProcess(["gh", *args], 127, "", "gh CLI is required")
    return subprocess.run(
        [gh, *args],
        check=False,
        text=True,
        capture_output=True,
        env=github_cli_env(),
    )  # nosec B603


def github_cli_env() -> dict[str, str] | None:
    token = _github_cli_token()
    if not token:
        return None
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env.pop("GITHUB_TOKEN", None)
    return env


def _github_cli_token() -> str:
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "APP_TOKEN", "AIO_FLEET_CHECK_TOKEN"):
        token = os.environ.get(key, "").strip()
        if token:
            return token
    return ""
