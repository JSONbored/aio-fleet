from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess  # nosec B404
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aio_fleet.github_app import read_urlopen_with_retry, resolve_token
from aio_fleet.manifest import RepoConfig

API_VERSION = "2022-11-28"


@dataclass(frozen=True)
class BranchCommitResult:
    action: str
    branch: str
    sha: str
    method: str
    verified: bool
    verification: dict[str, Any] = field(default_factory=dict)
    committed_paths: list[str] = field(default_factory=list)


def commit_paths_to_branch(
    repo: RepoConfig,
    *,
    branch: str,
    paths: list[str],
    message: str,
    base: str = "main",
    token: str | None = None,
    require_verified: bool = True,
    mode: str | None = None,
) -> BranchCommitResult:
    commit_mode = (
        mode or os.environ.get("AIO_FLEET_UPSTREAM_COMMIT_MODE", "api")
    ).strip()
    if commit_mode == "git-signed":
        return _commit_with_git(
            repo,
            branch=branch,
            paths=paths,
            message=message,
            base=base,
            token=token,
            require_verified=require_verified,
            sign=True,
        )
    if commit_mode != "api":
        raise ValueError(f"unsupported upstream commit mode: {commit_mode}")
    return _commit_with_contents_api(
        repo,
        branch=branch,
        paths=paths,
        message=message,
        base=base,
        token=token,
        require_verified=require_verified,
    )


def commit_verification(
    repo: RepoConfig,
    *,
    sha: str,
    token: str | None = None,
) -> dict[str, Any]:
    owner, repo_name = repo.github_repo.split("/", 1)
    response = _github_request(
        f"https://api.github.com/repos/{owner}/{repo_name}/commits/{sha}",
        token=_token(token),
        method="GET",
    )
    commit = response.get("commit", {})
    verification = commit.get("verification", {})
    return verification if isinstance(verification, dict) else {}


def _commit_with_contents_api(
    repo: RepoConfig,
    *,
    branch: str,
    paths: list[str],
    message: str,
    base: str,
    token: str | None,
    require_verified: bool,
) -> BranchCommitResult:
    token = _token(token)
    owner, repo_name = repo.github_repo.split("/", 1)
    branch_ref = f"heads/{branch}"
    base_sha = _ref_sha(owner, repo_name, f"heads/{base}", token=token)
    old_sha = _optional_ref_sha(owner, repo_name, branch_ref, token=token)
    created_branch = old_sha is None
    commit_shas: list[str] = []
    committed_paths: list[str] = []
    try:
        if old_sha:
            _update_ref(owner, repo_name, branch_ref, sha=base_sha, token=token)
        else:
            _create_ref(owner, repo_name, branch_ref, sha=base_sha, token=token)
        for relative_path in paths:
            local_path = repo.path / relative_path
            if not local_path.exists():
                raise RuntimeError(
                    f"{repo.name}: missing generated path: {relative_path}"
                )
            response = _put_contents(
                owner,
                repo_name,
                branch=branch,
                path=relative_path,
                content=local_path.read_bytes(),
                message=message,
                token=token,
            )
            commit = response.get("commit", {})
            sha = str(commit.get("sha") or "")
            if not sha:
                raise RuntimeError(f"{repo.name}: GitHub did not return a commit SHA")
            verification = _verification_from_response(repo, sha=sha, token=token)
            if require_verified and not verification.get("verified"):
                reason = verification.get("reason", "unknown")
                raise RuntimeError(
                    f"{repo.name}: GitHub API commit {sha} is not verified: {reason}"
                )
            commit_shas.append(sha)
            committed_paths.append(relative_path)
    except Exception:
        _restore_branch(
            owner,
            repo_name,
            branch_ref,
            old_sha=old_sha,
            created_branch=created_branch,
            token=token,
        )
        raise

    head = _ref_sha(owner, repo_name, branch_ref, token=token)
    verification = _verification_from_response(repo, sha=head, token=token)
    return BranchCommitResult(
        action="committed",
        branch=branch,
        sha=head,
        method="api",
        verified=bool(verification.get("verified")),
        verification=verification,
        committed_paths=committed_paths,
    )


def _commit_with_git(
    repo: RepoConfig,
    *,
    branch: str,
    paths: list[str],
    message: str,
    base: str,
    token: str | None,
    require_verified: bool,
    sign: bool,
) -> BranchCommitResult:
    _run_git(repo.path, ["fetch", "origin", base])
    _run_git(repo.path, ["checkout", "-B", branch, f"origin/{base}"])
    _run_git(repo.path, ["add", *paths])
    diff = _run_git(repo.path, ["diff", "--cached", "--quiet"], check=False)
    if diff.returncode == 0:
        sha = _run_git(repo.path, ["rev-parse", "HEAD"]).stdout.strip()
        verification = _verification_from_response(repo, sha=sha, token=token)
        return BranchCommitResult(
            action="no-diff",
            branch=branch,
            sha=sha,
            method="git-signed" if sign else "git",
            verified=bool(verification.get("verified")),
            verification=verification,
            committed_paths=[],
        )
    command = ["commit", "-m", message]
    if sign:
        command.insert(1, "-S")
    _run_git(repo.path, command)
    _run_git(repo.path, ["fetch", "origin", branch], check=False)
    _run_git(repo.path, ["push", "--force-with-lease", "-u", "origin", branch])
    sha = _run_git(repo.path, ["rev-parse", "HEAD"]).stdout.strip()
    verification = _verification_from_response(repo, sha=sha, token=token)
    if require_verified and not verification.get("verified"):
        reason = verification.get("reason", "unknown")
        raise RuntimeError(
            f"{repo.name}: pushed commit {sha} is not verified: {reason}"
        )
    return BranchCommitResult(
        action="committed",
        branch=branch,
        sha=sha,
        method="git-signed" if sign else "git",
        verified=bool(verification.get("verified")),
        verification=verification,
        committed_paths=paths,
    )


def _put_contents(
    owner: str,
    repo_name: str,
    *,
    branch: str,
    path: str,
    content: bytes,
    message: str,
    token: str,
) -> dict[str, Any]:
    current = _optional_contents(
        owner, repo_name, branch=branch, path=path, token=token
    )
    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": branch,
    }
    if current and current.get("sha"):
        payload["sha"] = current["sha"]
    return _github_request(
        f"https://api.github.com/repos/{owner}/{repo_name}/contents/{_quote_path(path)}",
        token=token,
        method="PUT",
        payload=payload,
    )


def _optional_contents(
    owner: str,
    repo_name: str,
    *,
    branch: str,
    path: str,
    token: str,
) -> dict[str, Any] | None:
    query = urllib.parse.urlencode({"ref": branch})
    try:
        response = _github_request(
            f"https://api.github.com/repos/{owner}/{repo_name}/contents/{_quote_path(path)}?{query}",
            token=token,
            method="GET",
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return response if isinstance(response, dict) else None


def _verification_from_response(
    repo: RepoConfig,
    *,
    sha: str,
    token: str | None,
) -> dict[str, Any]:
    verification = commit_verification(repo, sha=sha, token=token)
    return verification or {"verified": False, "reason": "missing-verification"}


def _ref_sha(owner: str, repo_name: str, ref: str, *, token: str) -> str:
    response = _github_request(
        f"https://api.github.com/repos/{owner}/{repo_name}/git/ref/{_quote_ref(ref)}",
        token=token,
        method="GET",
    )
    obj = response.get("object", {})
    sha = str(obj.get("sha") or "")
    if not sha:
        raise RuntimeError(f"{owner}/{repo_name}: unable to resolve ref {ref}")
    return sha


def _optional_ref_sha(
    owner: str,
    repo_name: str,
    ref: str,
    *,
    token: str,
) -> str | None:
    try:
        return _ref_sha(owner, repo_name, ref, token=token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _create_ref(
    owner: str,
    repo_name: str,
    ref: str,
    *,
    sha: str,
    token: str,
) -> None:
    _github_request(
        f"https://api.github.com/repos/{owner}/{repo_name}/git/refs",
        token=token,
        method="POST",
        payload={"ref": f"refs/{ref}", "sha": sha},
    )


def _update_ref(
    owner: str,
    repo_name: str,
    ref: str,
    *,
    sha: str,
    token: str,
) -> None:
    _github_request(
        f"https://api.github.com/repos/{owner}/{repo_name}/git/refs/{_quote_ref(ref)}",
        token=token,
        method="PATCH",
        payload={"sha": sha, "force": True},
    )


def _delete_ref(owner: str, repo_name: str, ref: str, *, token: str) -> None:
    _github_request(
        f"https://api.github.com/repos/{owner}/{repo_name}/git/refs/{_quote_ref(ref)}",
        token=token,
        method="DELETE",
    )


def _restore_branch(
    owner: str,
    repo_name: str,
    ref: str,
    *,
    old_sha: str | None,
    created_branch: bool,
    token: str,
) -> None:
    try:
        if created_branch:
            _delete_ref(owner, repo_name, ref, token=token)
        elif old_sha:
            _update_ref(owner, repo_name, ref, sha=old_sha, token=token)
    except Exception:
        return


def _github_request(
    url: str,
    *,
    token: str,
    method: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(  # nosec B310
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            **({"Content-Type": "application/json"} if payload is not None else {}),
        },
    )
    raw = read_urlopen_with_retry(request, timeout=30).decode("utf-8")
    return json.loads(raw or "{}")


def _run_git(
    cwd: Path, args: list[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git CLI is required")
    result = subprocess.run(  # nosec B603
        [git, *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result


def _quote_path(path: str) -> str:
    return urllib.parse.quote(path, safe="/")


def _quote_ref(ref: str) -> str:
    return urllib.parse.quote(ref, safe="/")


def _token(token: str | None) -> str:
    resolved = token or resolve_token(
        fallback_envs=("AIO_FLEET_CHECK_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
    )
    if not resolved:
        raise RuntimeError(
            "GitHub App credentials or a GitHub token are required for verified commits"
        )
    return resolved
