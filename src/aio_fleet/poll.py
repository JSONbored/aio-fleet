from __future__ import annotations

import json
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
                sha = str(pull_request.get("headRefOid") or "")
                number = str(pull_request.get("number") or "")
                if sha:
                    targets.append(
                        PollTarget(
                            repo=repo,
                            sha=sha,
                            event="pull_request",
                            source=f"pr:{number}",
                        )
                    )
        if include_main:
            sha = _main_sha(repo)
            if sha:
                targets.append(
                    PollTarget(repo=repo, sha=sha, event="push", source="main")
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
            "number,headRefOid",
        ]
    )
    if result.returncode != 0:
        return []
    return json.loads(result.stdout or "[]")


def _main_sha(repo: RepoConfig) -> str:
    result = _gh(["api", f"repos/{repo.github_repo}/commits/main", "--jq", ".sha"])
    return result.stdout.strip() if result.returncode == 0 else ""


def _gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    gh = shutil.which("gh")
    if gh is None:
        return subprocess.CompletedProcess(["gh", *args], 127, "", "gh CLI is required")
    return subprocess.run(
        [gh, *args], check=False, text=True, capture_output=True
    )  # nosec B603
