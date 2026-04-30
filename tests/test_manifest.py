from __future__ import annotations

from pathlib import Path

import pytest

from aio_fleet.manifest import ManifestError, load_manifest

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_loads_current_fleet() -> None:
    manifest = load_manifest(ROOT / "fleet.yml")

    assert manifest.owner == "JSONbored"  # nosec B101
    assert set(manifest.repos) == {  # nosec B101
        "unraid-aio-template",
        "sure-aio",
        "simplelogin-aio",
        "khoj-aio",
        "mem0-aio",
        "infisical-aio",
        "dify-aio",
        "signoz-aio",
    }


def test_manifest_records_known_fleet_exceptions() -> None:
    manifest = load_manifest(ROOT / "fleet.yml")

    assert manifest.repo("mem0-aio").get("checkout_submodules") is True  # nosec B101
    assert manifest.repo("dify-aio").extended_integration is not None  # nosec B101
    assert manifest.repo("signoz-aio").is_signoz_suite  # nosec B101
    assert (  # nosec B101
        manifest.repo("signoz-aio").get("upstream_digest_arg")
        == "UPSTREAM_SIGNOZ_DIGEST"
    )


def test_manifest_rejects_unknown_publish_profiles(tmp_path: Path) -> None:
    manifest_path = tmp_path / "fleet.yml"
    manifest_path.write_text("""
owner: JSONbored
repos:
  broken-aio:
    path: /tmp/broken-aio
    app_slug: broken-aio
    image_name: jsonbored/broken-aio
    docker_cache_scope: broken-aio-image
    pytest_image_tag: broken-aio:pytest
    publish_profile: mystery
""")

    with pytest.raises(ManifestError, match="unsupported publish_profile"):
        load_manifest(manifest_path)
