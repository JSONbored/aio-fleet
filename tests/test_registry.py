from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

from aio_fleet import registry
from aio_fleet.control_plane import registry_publish_command
from aio_fleet.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]


def test_compute_registry_tags_preserves_docker_hub_and_ghcr_tags(monkeypatch) -> None:
    repo = load_manifest(ROOT / "fleet.yml").repo("sure-aio")

    monkeypatch.setattr(
        registry, "_read_component_upstream_version", lambda *_: "0.7.0"
    )
    monkeypatch.setattr(
        registry, "_release_package_tag", lambda *_args, **_kwargs: "0.7.0-aio.1"
    )

    tags = registry.compute_registry_tags(repo, sha="a" * 40)

    assert tags.dockerhub == [  # nosec B101
        "jsonbored/sure-aio:latest",
        "jsonbored/sure-aio:0.7.0",
        "jsonbored/sure-aio:0.7.0-aio.1",
        f"jsonbored/sure-aio:sha-{'a' * 40}",
    ]
    assert tags.ghcr == [  # nosec B101
        "ghcr.io/jsonbored/sure-aio:latest",
        "ghcr.io/jsonbored/sure-aio:0.7.0",
        "ghcr.io/jsonbored/sure-aio:0.7.0-aio.1",
        f"ghcr.io/jsonbored/sure-aio:sha-{'a' * 40}",
    ]


def test_compute_registry_tags_tolerates_missing_release_commit(monkeypatch) -> None:
    repo = load_manifest(ROOT / "fleet.yml").repo("sure-aio")

    monkeypatch.setattr(
        registry, "_read_component_upstream_version", lambda *_: "0.7.0"
    )
    monkeypatch.setattr(
        registry, "latest_changelog_version", lambda *_args, **_kwargs: "0.7.0-aio.1"
    )
    monkeypatch.setattr(
        registry,
        "find_release_target_commit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit(1)),
    )

    tags = registry.compute_registry_tags(repo, sha="b" * 40)

    assert tags.dockerhub == [  # nosec B101
        "jsonbored/sure-aio:latest",
        "jsonbored/sure-aio:0.7.0",
        f"jsonbored/sure-aio:sha-{'b' * 40}",
    ]


def test_upstream_aio_track_release_tag_matches_changelog(monkeypatch) -> None:
    repo = load_manifest(ROOT / "fleet.yml").repo("sure-aio")
    sha = "c" * 40

    monkeypatch.setattr(
        registry, "_read_component_upstream_version", lambda *_: "0.7.0"
    )
    monkeypatch.setattr(
        registry, "latest_changelog_version", lambda *_args, **_kwargs: "0.7.0-aio.1"
    )
    monkeypatch.setattr(
        registry, "find_release_target_commit", lambda *_args, **_kwargs: sha
    )

    tags = registry.compute_registry_tags(repo, sha=sha)

    assert tags.release_package_tag == "0.7.0-aio.1"  # nosec B101
    assert "jsonbored/sure-aio:0.7.0-aio.1" in tags.dockerhub  # nosec B101
    assert "ghcr.io/jsonbored/sure-aio:0.7.0-aio.1" in tags.ghcr  # nosec B101


def test_signoz_agent_publish_command_uses_component_context(monkeypatch) -> None:
    repo = load_manifest(ROOT / "fleet.yml").repo("signoz-aio")

    monkeypatch.setattr(
        registry, "_read_component_upstream_version", lambda *_: "0.151.0"
    )
    monkeypatch.setattr(registry, "_release_package_tag", lambda *_args, **_kwargs: "")

    command = registry_publish_command(repo, sha="c" * 40, component="agent")

    assert "--file" in command  # nosec B101
    assert "components/signoz-agent/Dockerfile" in command  # nosec B101
    assert command[-1] == "components/signoz-agent"  # nosec B101
    assert "jsonbored/signoz-agent:0.151.0" in command  # nosec B101


def test_dockerhub_verification_uses_tag_api(monkeypatch) -> None:
    seen_urls: list[str] = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return b"{}"

    monkeypatch.setattr(registry.shutil, "which", lambda _name: "docker")
    monkeypatch.setattr(
        registry.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Docker Hub tags should not use docker inspect")
        ),
    )

    def fake_urlopen(url: str, timeout: int):
        seen_urls.append(url)
        assert timeout == 20  # nosec B101
        return Response()

    monkeypatch.setattr(registry.urllib.request, "urlopen", fake_urlopen)

    assert (
        registry.verify_registry_tags(["jsonbored/sure-aio:latest"]) == []
    )  # nosec B101
    assert seen_urls == [  # nosec B101
        "https://hub.docker.com/v2/repositories/jsonbored/sure-aio/tags/latest"
    ]


def test_dockerhub_verification_reports_missing_tag(monkeypatch) -> None:
    monkeypatch.setattr(registry.shutil, "which", lambda _name: "docker")

    def fake_urlopen(url: str, timeout: int):
        raise HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(registry.urllib.request, "urlopen", fake_urlopen)

    assert registry.verify_registry_tags(
        ["jsonbored/sure-aio:missing"]
    ) == [  # nosec B101
        "jsonbored/sure-aio:missing: tag not found on Docker Hub"
    ]


def test_ghcr_verification_uses_docker_imagetools(monkeypatch) -> None:
    seen_commands: list[list[str]] = []
    monkeypatch.setattr(registry.shutil, "which", lambda _name: "docker")

    def fake_run(command: list[str], **_kwargs):
        seen_commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(registry.subprocess, "run", fake_run)

    assert (
        registry.verify_registry_tags(["ghcr.io/jsonbored/sure-aio:latest"]) == []
    )  # nosec B101
    assert seen_commands == [  # nosec B101
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            "ghcr.io/jsonbored/sure-aio:latest",
        ]
    ]
