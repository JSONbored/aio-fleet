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


def test_sync_boilerplate_honors_repo_filters_and_templates(tmp_path: Path) -> None:
    config = tmp_path / "boilerplate.yml"
    source = tmp_path / "boilerplate" / "aio" / "SECURITY.md"
    source.parent.mkdir(parents=True)
    source.write_text("Report issues at https://github.com/{{ github_repo }}/security/policy\n")
    config.write_text(
        """
profiles:
  aio:
    files:
      - source: boilerplate/aio/SECURITY.md
        target: SECURITY.md
        template: true
        exclude_repos:
          - other-aio
"""
    )
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    changes = sync_boilerplate(_repo(repo_path), config_path=config, profile="aio", dry_run=False)

    assert len(changes) == 1  # nosec B101
    assert (repo_path / "SECURITY.md").read_text() == (  # nosec B101
        "Report issues at https://github.com/JSONbored/example-aio/security/policy\n"
    )


def test_sync_boilerplate_can_create_missing_only(tmp_path: Path) -> None:
    config = tmp_path / "boilerplate.yml"
    source = tmp_path / "boilerplate" / "aio" / "docs" / "releases.md"
    source.parent.mkdir(parents=True)
    source.write_text("canonical\n")
    config.write_text(
        """
profiles:
  aio:
    files:
      - source: boilerplate/aio/docs/releases.md
        target: docs/releases.md
        if_missing: true
"""
    )
    repo_path = tmp_path / "repo"
    (repo_path / "docs").mkdir(parents=True)
    (repo_path / "docs" / "releases.md").write_text("app-specific\n")

    changes = sync_boilerplate(_repo(repo_path), config_path=config, profile="aio", dry_run=False)

    assert changes == []  # nosec B101
    assert (repo_path / "docs" / "releases.md").read_text() == "app-specific\n"  # nosec B101
