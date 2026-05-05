from __future__ import annotations

from pathlib import Path

from aio_fleet import upstream
from aio_fleet.github_writer import BranchCommitResult
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


def test_upstream_monitor_write_updates_configured_submodule(
    tmp_path: Path, monkeypatch
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "openmemory").mkdir()
    dockerfile = repo_path / "Dockerfile"
    dockerfile.write_text("ARG UPSTREAM_VERSION=v2.0.0\n")
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
repos:
  mem0-aio:
    path: {repo_path}
    app_slug: mem0-aio
    image_name: jsonbored/mem0-aio
    docker_cache_scope: mem0-aio-image
    pytest_image_tag: mem0-aio:pytest
    upstream_monitor:
      - source: github-releases
        repo: mem0ai/mem0
        dockerfile: Dockerfile
        version_key: UPSTREAM_VERSION
        strategy: pr
        submodule_path: openmemory
        submodule_remote: fork
        submodule_ref_template: codex/openmemory-{{version}}-aio
""")
    calls: list[tuple[Path, list[str]]] = []

    monkeypatch.setattr(
        upstream,
        "latest_github_release_result",
        lambda *_args, **_kwargs: ("v2.0.1", ()),
    )
    monkeypatch.setattr(
        upstream,
        "run_git",
        lambda cwd, args, **_kwargs: calls.append((cwd, args)) or None,
    )

    result = upstream.monitor_repo(
        load_manifest(manifest).repo("mem0-aio"), write=True
    )[0]

    assert "ARG UPSTREAM_VERSION=v2.0.1" in dockerfile.read_text()  # nosec B101
    assert result.submodule_path == "openmemory"  # nosec B101
    assert result.submodule_ref == "codex/openmemory-v2.0.1-aio"  # nosec B101
    assert calls == [  # nosec B101
        (
            repo_path / "openmemory",
            ["fetch", "--tags", "fork", "codex/openmemory-v2.0.1-aio"],
        ),
        (repo_path / "openmemory", ["checkout", "--detach", "FETCH_HEAD"]),
    ]


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


def test_stable_filter_keeps_hotfix_and_excludes_alpha() -> None:
    versions = ["v0.7.0", "v0.7.0-hotfix.1", "v0.7.1-alpha.2"]

    filtered = upstream.filter_versions(versions, stable_only=True)

    assert filtered == ["v0.7.0", "v0.7.0-hotfix.1"]  # nosec B101
    assert sorted(filtered, key=upstream.version_sort_key)[-1] == (  # nosec B101
        "v0.7.0-hotfix.1"
    )


def test_github_releases_accept_stable_hotfix_and_report_prerelease_skips(
    monkeypatch,
) -> None:
    def fake_http_json(url: str, _headers=None):
        assert "repos/we-promise/sure/releases" in url  # nosec B101
        return [
            {"tag_name": "v0.7.1-alpha.2", "prerelease": True},
            {"tag_name": "v0.7.1-alpha.1", "prerelease": False},
            {"tag_name": "v0.7.0-hotfix.1", "prerelease": False},
            {"tag_name": "v0.7.0", "prerelease": False},
        ]

    monkeypatch.setattr(upstream, "http_json", fake_http_json)

    latest, skipped = upstream.latest_github_release_result(
        "we-promise/sure", stable_only=True, strip_prefix="v"
    )

    assert latest == "0.7.0-hotfix.1"  # nosec B101
    assert {item["version"]: item["reason"] for item in skipped} == {  # nosec B101
        "0.7.1-alpha.2": "github-prerelease",
        "0.7.1-alpha.1": "version-prerelease",
    }


def test_sure_hotfix_monitor_detects_stable_release_and_digest(
    tmp_path: Path, monkeypatch
) -> None:
    repo_path = tmp_path / "sure-aio"
    repo_path.mkdir()
    (repo_path / "Dockerfile").write_text(
        "ARG UPSTREAM_VERSION=0.7.0\n"
        "ARG UPSTREAM_IMAGE_DIGEST=sha256:old\n"
        "FROM ghcr.io/we-promise/sure:${UPSTREAM_VERSION}@${UPSTREAM_IMAGE_DIGEST}\n"
    )
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
repos:
  sure-aio:
    path: {repo_path}
    app_slug: sure-aio
    image_name: jsonbored/sure-aio
    docker_cache_scope: sure-aio-image
    pytest_image_tag: sure-aio:pytest
    upstream_monitor:
      - component: aio
        name: Sure
        source: github-releases
        repo: we-promise/sure
        image: we-promise/sure
        digest_source: ghcr
        dockerfile: Dockerfile
        version_key: UPSTREAM_VERSION
        version_strip_prefix: v
        digest_key: UPSTREAM_IMAGE_DIGEST
        stable_only: true
        strategy: pr
""")

    def fake_http_json(url: str, _headers=None):
        assert "repos/we-promise/sure/releases" in url  # nosec B101
        return [
            {"tag_name": "v0.7.1-alpha.2", "prerelease": True},
            {"tag_name": "v0.7.0-hotfix.1", "prerelease": False},
            {"tag_name": "v0.7.0", "prerelease": False},
        ]

    def fake_digest(
        image: str, version: str, *, registry: str, prefix: str = ""
    ) -> str:
        assert image == "we-promise/sure"  # nosec B101
        assert version == "0.7.0-hotfix.1"  # nosec B101
        assert registry == "ghcr"  # nosec B101
        assert prefix == ""  # nosec B101
        return "sha256:f49fc95b95706fcb7752466edef3c902ba9a746ed6b8ae1206ff22e180ac5006"

    monkeypatch.setattr(upstream, "http_json", fake_http_json)
    monkeypatch.setattr(upstream, "registry_digest_for_version", fake_digest)

    result = upstream.monitor_repo(load_manifest(manifest).repo("sure-aio"))[0]

    assert result.latest_version == "0.7.0-hotfix.1"  # nosec B101
    assert result.version_update is True  # nosec B101
    assert result.latest_digest == (  # nosec B101
        "sha256:f49fc95b95706fcb7752466edef3c902ba9a746ed6b8ae1206ff22e180ac5006"
    )
    assert result.skipped_versions == (  # nosec B101
        {"version": "0.7.1-alpha.2", "reason": "github-prerelease"},
    )


def test_digest_lookup_tries_unprefixed_hotfix_image_tag(monkeypatch) -> None:
    seen: list[str] = []

    def fake_digest(_image: str, tag: str, *, registry: str) -> str:
        assert registry == "ghcr"  # nosec B101
        seen.append(tag)
        return "sha256:hotfix" if tag == "0.7.0-hotfix.1" else ""

    monkeypatch.setattr(upstream, "registry_digest", fake_digest)

    digest = upstream.registry_digest_for_version(
        "we-promise/sure", "0.7.0-hotfix.1", registry="ghcr"
    )

    assert digest == "sha256:hotfix"  # nosec B101
    assert seen[0] == "0.7.0-hotfix.1"  # nosec B101


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


def test_create_upstream_pr_uses_verified_commit_writer(
    tmp_path: Path, monkeypatch
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "Dockerfile").write_text("ARG UPSTREAM_VERSION=1.1.0\n")
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
    repo = load_manifest(manifest).repo("example-aio")
    result = upstream.UpstreamMonitorResult(
        repo="example-aio",
        component="aio",
        name="Example",
        strategy="pr",
        source="github-tags",
        current_version="1.0.0",
        latest_version="1.1.0",
        current_digest="",
        latest_digest="",
        version_update=True,
        digest_update=False,
        dockerfile=repo_path / "Dockerfile",
        version_key="UPSTREAM_VERSION",
        digest_key="",
        release_notes_url="https://example.invalid/releases",
    )
    seen: dict[str, object] = {}

    def fake_commit(*_args, **kwargs) -> BranchCommitResult:
        seen.update(kwargs)
        return BranchCommitResult(
            action="committed",
            branch=str(kwargs["branch"]),
            sha="a" * 40,
            method="api",
            verified=True,
            verification={"verified": True, "reason": "valid"},
            committed_paths=list(kwargs["paths"]),
        )

    monkeypatch.setattr(upstream, "commit_paths_to_branch", fake_commit)
    monkeypatch.setattr(upstream, "upsert_pr", lambda *_args, **_kwargs: "https://pr")
    monkeypatch.setattr(
        upstream, "close_superseded_upstream_prs", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(upstream, "upsert_check_run", lambda *_args, **_kwargs: None)

    action = upstream.create_or_update_upstream_pr(
        repo, [result], dry_run=False, post_check=True
    )

    assert seen["branch"] == "codex/upstream-example-aio-1.1.0"  # nosec B101
    assert seen["paths"] == ["Dockerfile"]  # nosec B101
    assert seen["require_verified"] is True  # nosec B101
    assert action["verified"] is True  # nosec B101
    assert action["sha"] == "a" * 40  # nosec B101


def test_upstream_body_mentions_source_first_catalog_sync(tmp_path: Path) -> None:
    repo = load_manifest(_minimal_manifest(tmp_path)).repo("example-aio")
    result = upstream.UpstreamMonitorResult(
        repo="example-aio",
        component="aio",
        name="Example",
        strategy="pr",
        source="github-tags",
        current_version="1.0.0",
        latest_version="1.1.0",
        current_digest="",
        latest_digest="",
        version_update=True,
        digest_update=False,
        dockerfile=repo.path / "Dockerfile",
        version_key="UPSTREAM_VERSION",
        digest_key="",
        release_notes_url="https://example.invalid/releases",
    )

    body = upstream.upstream_body(repo, [result])

    assert "catalog sync follows the validated source repo" in body  # nosec B101
    assert "Release notes: https://example.invalid/releases" in body  # nosec B101


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
