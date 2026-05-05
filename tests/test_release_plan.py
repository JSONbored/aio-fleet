from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from aio_fleet.manifest import RepoConfig
from aio_fleet.release_plan import release_plan_for_repo


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
