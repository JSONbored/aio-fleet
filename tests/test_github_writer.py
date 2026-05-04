from __future__ import annotations

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
