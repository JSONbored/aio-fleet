from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest

from aio_fleet import github_writer
from aio_fleet.manifest import load_manifest


def test_contents_api_writer_requires_verified_commits(
    tmp_path: Path, monkeypatch
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "Dockerfile").write_text("ARG UPSTREAM_VERSION=1.1.0\n")
    repo = load_manifest(_manifest(tmp_path, repo_path)).repo("example-aio")
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_request(
        url: str,
        *,
        token: str,
        method: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        calls.append((method, url, payload))
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "m" * 40}}
        if method == "GET" and url.endswith("/git/ref/heads/codex/update"):
            return {"object": {"sha": "o" * 40}}
        if method == "PATCH":
            return {}
        if method == "GET" and "/contents/Dockerfile" in url:
            return {"sha": "blob"}
        if method == "PUT":
            return {"commit": {"sha": "n" * 40}}
        if method == "GET" and f"/commits/{'n' * 40}" in url:
            return {
                "commit": {"verification": {"verified": False, "reason": "unsigned"}}
            }
        raise AssertionError((method, url, payload))

    monkeypatch.setattr(github_writer, "_github_request", fake_request)
    api_token = "dummy-token"  # nosec B105

    with pytest.raises(RuntimeError, match="not verified: unsigned"):
        github_writer.commit_paths_to_branch(
            repo,
            branch="codex/update",
            paths=["Dockerfile"],
            message="chore(sync): bump example",
            token=api_token,
        )

    restore = [
        payload
        for method, _url, payload in calls
        if method == "PATCH" and payload and payload.get("sha") == "o" * 40
    ]
    assert restore  # nosec B101


def test_contents_api_writer_returns_verified_head(tmp_path: Path, monkeypatch) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "Dockerfile").write_text("ARG UPSTREAM_VERSION=1.1.0\n")
    repo = load_manifest(_manifest(tmp_path, repo_path)).repo("example-aio")

    def fake_request(
        url: str,
        *,
        token: str,
        method: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "m" * 40}}
        if method == "GET" and url.endswith("/git/ref/heads/codex/update"):
            return {"object": {"sha": "h" * 40}}
        if method == "PATCH":
            return {}
        if method == "GET" and "/contents/Dockerfile" in url:
            return {"sha": "blob"}
        if method == "PUT":
            return {"commit": {"sha": "h" * 40}}
        if method == "GET" and f"/commits/{'h' * 40}" in url:
            return {"commit": {"verification": {"verified": True, "reason": "valid"}}}
        raise AssertionError((method, url, payload))

    monkeypatch.setattr(github_writer, "_github_request", fake_request)
    api_token = "dummy-token"  # nosec B105

    result = github_writer.commit_paths_to_branch(
        repo,
        branch="codex/update",
        paths=["Dockerfile"],
        message="chore(sync): bump example",
        token=api_token,
    )

    assert result.sha == "h" * 40  # nosec B101
    assert result.verified is True  # nosec B101
    assert result.method == "api"  # nosec B101


def test_api_writer_commits_submodule_gitlinks(tmp_path: Path, monkeypatch) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    github_writer._run_git(repo_path, ["init"])
    github_writer._run_git(
        repo_path,
        [
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{'b' * 40},openmemory",
        ],
    )
    (repo_path / "Dockerfile").write_text("ARG UPSTREAM_VERSION=v2.0.1\n")
    repo = load_manifest(_manifest(tmp_path, repo_path)).repo("example-aio")
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_request(
        url: str,
        *,
        token: str,
        method: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        calls.append((method, url, payload))
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "m" * 40}}
        if method == "GET" and url.endswith("/git/ref/heads/codex/update"):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if method == "GET" and url.endswith(f"/git/commits/{'m' * 40}"):
            return {"tree": {"sha": "t" * 40}}
        if method == "POST" and url.endswith("/git/blobs"):
            return {"sha": "d" * 40}
        if method == "POST" and url.endswith("/git/trees"):
            assert payload is not None  # nosec B101
            entries = payload["tree"]
            assert entries == [  # nosec B101
                {
                    "path": "Dockerfile",
                    "mode": "100644",
                    "type": "blob",
                    "sha": "d" * 40,
                },
                {
                    "path": "openmemory",
                    "mode": "160000",
                    "type": "commit",
                    "sha": "b" * 40,
                },
            ]
            return {"sha": "n" * 40}
        if method == "POST" and url.endswith("/git/commits"):
            assert payload is not None  # nosec B101
            assert "author" not in payload  # nosec B101
            assert "committer" not in payload  # nosec B101
            assert payload["parents"] == ["m" * 40]  # nosec B101
            return {"sha": "c" * 40}
        if method == "POST" and url.endswith("/git/refs"):
            return {}
        if method == "GET" and f"/commits/{'c' * 40}" in url:
            return {"commit": {"verification": {"verified": True, "reason": "valid"}}}
        raise AssertionError((method, url, payload))

    monkeypatch.setattr(github_writer, "_github_request", fake_request)
    api_token = "dummy-token"  # nosec B105

    result = github_writer.commit_paths_to_branch(
        repo,
        branch="codex/update",
        paths=["Dockerfile", "openmemory"],
        message="chore(sync): bump mem0",
        token=api_token,
    )

    assert result.sha == "c" * 40  # nosec B101
    assert result.verified is True  # nosec B101
    assert result.committed_paths == ["Dockerfile", "openmemory"]  # nosec B101
    assert any(
        url.endswith("/git/trees") for _method, url, _payload in calls
    )  # nosec B101


def _manifest(tmp_path: Path, repo_path: Path) -> Path:
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
repos:
  example-aio:
    path: {repo_path}
    app_slug: example-aio
    image_name: jsonbored/example-aio
    docker_cache_scope: example-aio-image
    pytest_image_tag: example-aio:pytest
""")
    return manifest
