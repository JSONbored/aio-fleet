from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404
from dataclasses import dataclass

from aio_fleet.manifest import FleetManifest, RepoConfig


@dataclass(frozen=True)
class PollTarget:
    repo: RepoConfig
    sha: str
    event: str
    source: str
    checkout_submodules: bool = False


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
                            checkout_submodules=False,
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
                    )
                )
    return targets


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
