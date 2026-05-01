from __future__ import annotations

from pathlib import Path

from aio_fleet.changelog import (
    build_changes_body,
    encode_for_template,
    render_git_cliff_config,
)
from aio_fleet.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]


def test_render_git_cliff_config_uses_aio_tag_pattern() -> None:
    repo = load_manifest(ROOT / "fleet.yml").repo("sure-aio")

    rendered = render_git_cliff_config(repo)

    assert "tag_pattern = '^v?[0-9].*-aio\\.[0-9]+$'" in rendered  # nosec B101


def test_build_changes_body_filters_markdown_structure(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## 1.2.3-aio.1 - 2026-05-01\n")

    body = build_changes_body(
        "1.2.3-aio.1",
        "### Features\n- Added thing\n\nFull Changelog: https://example.invalid",
        changelog,
    )

    assert body == "\n".join(  # nosec B101
        [
            "### 2026-05-01",
            "- Generated from CHANGELOG.md during release preparation. Do not edit manually.",
            "- Added thing",
        ]
    )
    assert "&#xD;" in encode_for_template(body)  # nosec B101
