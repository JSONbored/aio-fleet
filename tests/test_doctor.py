from __future__ import annotations

from pathlib import Path

from aio_fleet.manifest import RepoConfig
from aio_fleet.validators import catalog_asset_failures


def _repo(tmp_path: Path, catalog_assets: list[dict[str, str]]) -> RepoConfig:
    raw = {
        "path": str(tmp_path),
        "app_slug": "mem0-aio",
        "image_name": "jsonbored/mem0-aio",
        "docker_cache_scope": "mem0-aio-image",
        "pytest_image_tag": "mem0-aio:pytest",
        "catalog_assets": catalog_assets,
    }
    return RepoConfig(name="mem0-aio", raw=raw, defaults={}, owner="JSONbored")


def _write_mem0_xml(tmp_path: Path) -> None:
    (tmp_path / "mem0-aio.xml").write_text(
        """<?xml version="1.0"?>
<Container version="2">
  <Icon>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/icons/mem0.jpeg</Icon>
</Container>
"""
    )


def test_catalog_asset_check_accepts_matching_icon_source(tmp_path: Path) -> None:
    _write_mem0_xml(tmp_path)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "mem0.jpeg").write_bytes(b"icon")

    failures = catalog_asset_failures(
        _repo(
            tmp_path,
            [
                {"source": "mem0-aio.xml", "target": "mem0-aio.xml"},
                {"source": "assets/mem0.jpeg", "target": "icons/mem0.jpeg"},
            ],
        )
    )

    assert failures == []  # nosec B101


def test_catalog_asset_check_rejects_missing_and_mismatched_icon(tmp_path: Path) -> None:
    _write_mem0_xml(tmp_path)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "mem0.jpeg").write_bytes(b"icon")

    failures = catalog_asset_failures(
        _repo(
            tmp_path,
            [
                {"source": "mem0-aio.xml", "target": "mem0-aio.xml"},
                {"source": "assets/app-icon.png", "target": "icons/mem0.png"},
            ],
        )
    )

    assert "mem0-aio: catalog_assets source missing: assets/app-icon.png" in failures  # nosec B101
    assert (  # nosec B101
        "mem0-aio: catalog_assets target icons/mem0.png "
        "is not referenced by any catalog XML Icon"
    ) in failures
