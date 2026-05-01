from __future__ import annotations

from pathlib import Path

from aio_fleet import registry
from aio_fleet.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]


def test_compute_registry_tags_preserves_docker_hub_and_ghcr_tags(monkeypatch) -> None:
    repo = load_manifest(ROOT / "fleet.yml").repo("sure-aio")

    monkeypatch.setattr(
        registry, "_read_component_upstream_version", lambda *_: "0.7.0"
    )
    monkeypatch.setattr(
        registry, "_release_package_tag", lambda *_args, **_kwargs: "0.7.0-aio-v1"
    )

    tags = registry.compute_registry_tags(repo, sha="a" * 40)

    assert tags.dockerhub == [  # nosec B101
        "jsonbored/sure-aio:latest",
        "jsonbored/sure-aio:0.7.0",
        "jsonbored/sure-aio:0.7.0-aio-v1",
        f"jsonbored/sure-aio:sha-{'a' * 40}",
    ]
    assert tags.ghcr == [  # nosec B101
        "ghcr.io/jsonbored/sure-aio:latest",
        "ghcr.io/jsonbored/sure-aio:0.7.0",
        "ghcr.io/jsonbored/sure-aio:0.7.0-aio-v1",
        f"ghcr.io/jsonbored/sure-aio:sha-{'a' * 40}",
    ]
