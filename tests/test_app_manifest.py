from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aio_fleet.app_manifest import (
    APP_MANIFEST_NAME,
    load_app_manifest,
    render_app_manifest,
    validate_app_manifest,
)
from aio_fleet.manifest import ManifestError, load_manifest

ROOT = Path(__file__).resolve().parents[1]


def test_render_app_manifest_exports_sure_control_plane_surface() -> None:
    repo = load_manifest(ROOT / "fleet.yml").repo("sure-aio")

    rendered = yaml.safe_load(render_app_manifest(repo))

    assert rendered["schema_version"] == 1  # nosec B101
    assert rendered["repo"] == "sure-aio"  # nosec B101
    assert rendered["image"]["name"] == "jsonbored/sure-aio"  # nosec B101
    assert rendered["release"]["profile"] == "upstream-aio-track"  # nosec B101
    assert rendered["template"]["xml_paths"] == ["sure-aio.xml"]  # nosec B101
    assert rendered["catalog"]["assets"] == [  # nosec B101
        {"source": "sure-aio.xml", "target": "sure-aio.xml"}
    ]


def test_load_app_manifest_validates_required_sections(tmp_path: Path) -> None:
    path = tmp_path / APP_MANIFEST_NAME
    path.write_text("""
schema_version: 1
repo: example-aio
github_repo: JSONbored/example-aio
app_slug: example-aio
image:
  name: jsonbored/example-aio
  cache_scope: example-aio-image
  pytest_tag: example-aio:pytest
release:
  name: Example AIO
  profile: changelog-version
""")

    manifest = load_app_manifest(path)

    assert manifest["repo"] == "example-aio"  # nosec B101


def test_validate_app_manifest_rejects_missing_image_tag() -> None:
    with pytest.raises(ManifestError, match="image missing required key: pytest_tag"):
        validate_app_manifest(
            {
                "schema_version": 1,
                "repo": "example-aio",
                "github_repo": "JSONbored/example-aio",
                "app_slug": "example-aio",
                "image": {
                    "name": "jsonbored/example-aio",
                    "cache_scope": "example-aio-image",
                },
                "release": {"name": "Example AIO", "profile": "changelog-version"},
            }
        )
