from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from aio_fleet import release_plan as release_plan_module
from aio_fleet.manifest import RepoConfig, load_manifest
from aio_fleet.release_plan import release_plan_for_manifest, release_plan_for_repo


def test_release_plan_classifies_publish_missing(tmp_path: Path, monkeypatch) -> None:
    repo = RepoConfig(
        name="sure-aio",
        raw={
            "path": str(tmp_path),
            "app_slug": "sure-aio",
            "image_name": "jsonbored/sure-aio",
            "docker_cache_scope": "sure-aio-image",
            "pytest_image_tag": "sure-aio:pytest",
            "publish_profile": "upstream-aio-track",
        },
        defaults={},
        owner="JSONbored",
    )
    monkeypatch.setattr("aio_fleet.release_plan._git_head", lambda _path: "a" * 40)
    monkeypatch.setattr(
        "aio_fleet.release_plan._safe_latest_aio_tag", lambda _repo: "0.7.0-aio.1"
    )
    monkeypatch.setattr(
        "aio_fleet.release_plan._safe_next_aio", lambda _repo: "0.7.0-aio.2"
    )
    monkeypatch.setattr(
        "aio_fleet.release_plan._safe_has_aio_changes", lambda _repo: False
    )
    monkeypatch.setattr(
        "aio_fleet.release_plan._safe_changelog_version", lambda _repo: "0.7.0-aio.1"
    )
    monkeypatch.setattr(
        "aio_fleet.release_plan._latest_github_release",
        lambda _repo: {"state": "ok", "tag": "0.7.0-aio.1"},
    )
    monkeypatch.setattr(
        "aio_fleet.release_plan.compute_registry_tags",
        lambda *_args, **_kwargs: SimpleNamespace(
            dockerhub=["jsonbored/sure-aio:latest"],
            ghcr=["ghcr.io/jsonbored/sure-aio:latest"],
            all_tags=["jsonbored/sure-aio:latest"],
        ),
    )
    monkeypatch.setattr(
        "aio_fleet.release_plan.verify_registry_tags",
        lambda _tags: ["jsonbored/sure-aio:latest: missing"],
    )

    plan = release_plan_for_repo(repo, include_registry=True)

    assert plan["state"] == "publish-missing"  # nosec B101
    assert plan["blockers"] == ["missing or unreachable registry tags"]  # nosec B101


def test_release_plan_classifies_catalog_sync_needed(
    tmp_path: Path, monkeypatch
) -> None:
    repo = RepoConfig(
        name="dify-aio",
        raw={
            "path": str(tmp_path),
            "app_slug": "dify-aio",
            "image_name": "jsonbored/dify-aio",
            "docker_cache_scope": "dify-aio-image",
            "pytest_image_tag": "dify-aio:pytest",
            "publish_profile": "upstream-aio-track",
        },
        defaults={},
        owner="JSONbored",
    )
    monkeypatch.setattr("aio_fleet.release_plan._git_head", lambda _path: "b" * 40)
    monkeypatch.setattr(
        "aio_fleet.release_plan._safe_latest_aio_tag", lambda _repo: "1.14.0-aio.2"
    )
    monkeypatch.setattr(
        "aio_fleet.release_plan._safe_next_aio", lambda _repo: "1.14.0-aio.3"
    )
    monkeypatch.setattr(
        "aio_fleet.release_plan._safe_has_aio_changes", lambda _repo: False
    )
    monkeypatch.setattr(
        "aio_fleet.release_plan._safe_changelog_version", lambda _repo: "1.14.0-aio.2"
    )
    monkeypatch.setattr(
        "aio_fleet.release_plan._latest_github_release",
        lambda _repo: {"state": "ok", "tag": "1.14.0-aio.2"},
    )

    plan = release_plan_for_repo(repo, catalog_sync_needed=True)

    assert plan["state"] == "catalog-sync-needed"  # nosec B101
    assert plan["catalog_sync_needed"] is True  # nosec B101


def test_release_plan_redacts_private_manifest_repos(
    tmp_path: Path, monkeypatch
) -> None:
    private_path = tmp_path / "private-service-aio"
    public_path = tmp_path / "public-service-aio"
    private_path.mkdir()
    public_path.mkdir()
    manifest_path = tmp_path / "fleet.yml"
    manifest_path.write_text(f"""
owner: JSONbored
repos:
  private-service-aio:
    path: {private_path}
    github_repo: PrivateOrg/private-service-aio
    public: false
    app_slug: private-service-aio
    image_name: jsonbored/private-service-aio
    docker_cache_scope: private-service-aio-image
    pytest_image_tag: private-service-aio:pytest
  public-service-aio:
    path: {public_path}
    github_repo: JSONbored/public-service-aio
    public: true
    app_slug: public-service-aio
    image_name: jsonbored/public-service-aio
    docker_cache_scope: public-service-aio-image
    pytest_image_tag: public-service-aio:pytest
""")
    calls: list[str] = []

    def fake_release_plan(repo: RepoConfig, **_kwargs):
        calls.append(repo.name)
        return {
            "repo": repo.name,
            "profile": repo.publish_profile,
            "sha": "e" * 40,
            "latest_release_tag": "1.0.0-aio.1",
            "latest_changelog_version": "1.0.0-aio.1",
            "latest_github_release": {
                "state": "ok",
                "tag": "1.0.0-aio.1",
                "url": f"https://github.com/{repo.github_repo}/releases/tag/1.0.0-aio.1",
            },
            "next_version": "",
            "release_due": False,
            "catalog_sync_needed": False,
            "registry_state": "ok",
            "registry_tags": {"dockerhub": [], "ghcr": []},
            "registry_failures": [],
            "state": "current",
            "blockers": [],
            "warnings": [],
            "next_action": "none",
        }

    monkeypatch.setattr(
        "aio_fleet.release_plan.release_plan_for_repo", fake_release_plan
    )

    rows = release_plan_for_manifest(load_manifest(manifest_path), redact_private=True)

    private_row = next(row for row in rows if row["repo"] == "private-service-aio")
    public_row = next(row for row in rows if row["repo"] == "public-service-aio")
    assert calls == ["public-service-aio"]  # nosec B101
    assert private_row["state"] == "private-skipped"  # nosec B101
    assert private_row["sha"] == ""  # nosec B101
    assert private_row["latest_github_release"] == {  # nosec B101
        "state": "private-skipped"
    }
    assert public_row["sha"] == "e" * 40  # nosec B101


def test_latest_github_release_uses_dashboard_token(
    monkeypatch, tmp_path: Path
) -> None:
    repo = RepoConfig(
        name="sure-aio",
        raw={
            "path": str(tmp_path),
            "github_repo": "JSONbored/sure-aio",
            "app_slug": "sure-aio",
            "image_name": "jsonbored/sure-aio",
            "docker_cache_scope": "sure-aio-image",
            "pytest_image_tag": "sure-aio:pytest",
        },
        defaults={},
        owner="JSONbored",
    )
    captured_env: dict[str, str] = {}

    def fake_run(*args: object, **kwargs: object):
        nonlocal captured_env
        captured_env = dict(kwargs.get("env") or {})
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(
                {
                    "tagName": "1.0.0-aio.1",
                    "publishedAt": "2026-05-13T00:00:00Z",
                    "targetCommitish": "a" * 40,
                    "url": "https://github.com/JSONbored/sure-aio/releases/tag/1.0.0-aio.1",
                }
            ),
            stderr="",
        )

    monkeypatch.setenv("AIO_FLEET_DASHBOARD_TOKEN", "dashboard-token")
    monkeypatch.setenv("AIO_FLEET_UPSTREAM_TOKEN", "upstream-token")
    monkeypatch.setenv("GH_TOKEN", "lower-priority-token")
    monkeypatch.setenv("GITHUB_TOKEN", "repo-token")
    monkeypatch.setattr(release_plan_module.subprocess, "run", fake_run)

    result = release_plan_module._latest_github_release(repo)

    assert result["state"] == "ok"  # nosec B101
    assert captured_env["GH_TOKEN"] == "dashboard-token"  # nosec B101
    assert "AIO_FLEET_DASHBOARD_TOKEN" not in captured_env  # nosec B101
    assert "GITHUB_TOKEN" not in captured_env  # nosec B101
