from __future__ import annotations

from pathlib import Path

from aio_fleet.boilerplate import sync_boilerplate
from aio_fleet.manifest import RepoConfig


def _repo(path: Path) -> RepoConfig:
    raw = {
        "path": str(path),
        "app_slug": "example-aio",
        "image_name": "jsonbored/example-aio",
        "docker_cache_scope": "example-aio-image",
        "pytest_image_tag": "example-aio:pytest",
    }
    return RepoConfig(name="example-aio", raw=raw, defaults={}, owner="JSONbored")


def test_sync_boilerplate_dry_run_reports_without_writing(tmp_path: Path) -> None:
    config = tmp_path / "boilerplate.yml"
    source = tmp_path / "boilerplate" / "aio" / ".github" / "pull_request_template.md"
    source.parent.mkdir(parents=True)
    source.write_text("## Summary\n")
    config.write_text(
        """
profiles:
  aio:
    files:
      - source: boilerplate/aio/.github/pull_request_template.md
        target: .github/pull_request_template.md
"""
    )
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    changes = sync_boilerplate(_repo(repo_path), config_path=config, profile="aio", dry_run=True)

    assert len(changes) == 1  # nosec B101
    assert changes[0].action == "create"  # nosec B101
    assert not (repo_path / ".github" / "pull_request_template.md").exists()  # nosec B101


def test_sync_boilerplate_writes_changed_files(tmp_path: Path) -> None:
    config = tmp_path / "boilerplate.yml"
    source = tmp_path / "boilerplate" / "aio" / ".github" / "pull_request_template.md"
    source.parent.mkdir(parents=True)
    source.write_text("## Summary\n")
    config.write_text(
        """
profiles:
  aio:
    files:
      - source: boilerplate/aio/.github/pull_request_template.md
        target: .github/pull_request_template.md
"""
    )
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    changes = sync_boilerplate(_repo(repo_path), config_path=config, profile="aio", dry_run=False)

    assert len(changes) == 1  # nosec B101
    assert (repo_path / ".github" / "pull_request_template.md").read_text() == "## Summary\n"  # nosec B101
