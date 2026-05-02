from __future__ import annotations

from pathlib import Path

from aio_fleet import upstream
from aio_fleet.manifest import load_manifest


def test_upstream_monitor_detects_version_and_digest_update(
    tmp_path: Path, monkeypatch
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "Dockerfile").write_text(
        "ARG UPSTREAM_VERSION=1.0.0\n"
        "ARG UPSTREAM_IMAGE_DIGEST=sha256:old\n"
        "FROM example/app:${UPSTREAM_VERSION}@${UPSTREAM_IMAGE_DIGEST}\n"
    )
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
    upstream_monitor:
      - component: aio
        name: Example
        source: github-tags
        repo: example/app
        image: example/app
        digest_source: dockerhub
        dockerfile: Dockerfile
        version_key: UPSTREAM_VERSION
        digest_key: UPSTREAM_IMAGE_DIGEST
        strategy: pr
""")

    monkeypatch.setattr(
        upstream, "latest_github_tag", lambda *_args, **_kwargs: "1.1.0"
    )
    monkeypatch.setattr(
        upstream, "registry_digest_for_version", lambda *_args, **_kwargs: "sha256:new"
    )

    result = upstream.monitor_repo(load_manifest(manifest).repo("example-aio"))[0]

    assert result.version_update is True  # nosec B101
    assert result.digest_update is True  # nosec B101
    assert result.latest_version == "1.1.0"  # nosec B101
    assert result.latest_digest == "sha256:new"  # nosec B101


def test_upstream_monitor_write_updates_dockerfile(tmp_path: Path, monkeypatch) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    dockerfile = repo_path / "Dockerfile"
    dockerfile.write_text(
        "ARG UPSTREAM_VERSION=1.0.0\nARG UPSTREAM_IMAGE_DIGEST=sha256:old\n"
    )
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
    upstream_monitor:
      - source: github-tags
        repo: example/app
        image: example/app
        digest_source: dockerhub
        dockerfile: Dockerfile
        version_key: UPSTREAM_VERSION
        digest_key: UPSTREAM_IMAGE_DIGEST
        strategy: pr
""")

    monkeypatch.setattr(
        upstream, "latest_github_tag", lambda *_args, **_kwargs: "1.1.0"
    )
    monkeypatch.setattr(
        upstream, "registry_digest_for_version", lambda *_args, **_kwargs: "sha256:new"
    )

    upstream.monitor_repo(load_manifest(manifest).repo("example-aio"), write=True)

    assert "ARG UPSTREAM_VERSION=1.1.0" in dockerfile.read_text()  # nosec B101
    assert (
        "ARG UPSTREAM_IMAGE_DIGEST=sha256:new" in dockerfile.read_text()
    )  # nosec B101


def test_upstream_monitor_does_not_write_notify_strategy(
    tmp_path: Path, monkeypatch
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    dockerfile = repo_path / "Dockerfile"
    dockerfile.write_text("ARG UPSTREAM_VERSION=1.0.0\n")
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
    upstream_monitor:
      - source: github-tags
        repo: example/app
        dockerfile: Dockerfile
        version_key: UPSTREAM_VERSION
        strategy: notify
""")

    monkeypatch.setattr(
        upstream, "latest_github_tag", lambda *_args, **_kwargs: "1.1.0"
    )

    result = upstream.monitor_repo(
        load_manifest(manifest).repo("example-aio"), write=True
    )

    assert result[0].updates_available is True  # nosec B101
    assert "ARG UPSTREAM_VERSION=1.0.0" in dockerfile.read_text()  # nosec B101


def test_create_upstream_pr_skips_notify_only_updates(tmp_path: Path) -> None:
    result = upstream.UpstreamMonitorResult(
        repo="example-aio",
        component="aio",
        name="Example",
        strategy="notify",
        source="github-tags",
        current_version="1.0.0",
        latest_version="1.1.0",
        current_digest="",
        latest_digest="",
        version_update=True,
        digest_update=False,
        dockerfile=Path("Dockerfile"),
        version_key="UPSTREAM_VERSION",
        digest_key="",
        release_notes_url="https://example.invalid/releases",
    )

    action = upstream.create_or_update_upstream_pr(
        load_manifest(_minimal_manifest(tmp_path)).repo("example-aio"),
        [result],
        dry_run=True,
        post_check=True,
    )

    assert action == {  # nosec B101
        "repo": "example-aio",
        "action": "skipped",
        "reason": "no-pr-strategy-updates",
    }


def _minimal_manifest(repo_path: Path) -> Path:
    manifest = repo_path / "fleet.yml"
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
