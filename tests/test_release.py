from __future__ import annotations

import subprocess  # nosec B404
from pathlib import Path

from aio_fleet.release import (
    find_release_publish_target_commit,
    latest_changelog_version,
    main,
    next_aio_release_version,
    next_semver_release_version,
    read_upstream_version,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)  # nosec


def _git_output(repo: Path, *args: str) -> str:
    return subprocess.check_output(  # nosec B603 B607
        ["git", *args], cwd=repo, text=True
    ).strip()


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def _init_repo(repo: Path) -> None:
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Tests")
    _git(repo, "config", "commit.gpgsign", "false")


def test_release_helpers_read_declarative_upstream_version(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    upstream = tmp_path / "upstream.toml"
    dockerfile.write_text("ARG UPSTREAM_APP_VERSION=v1.2.3@sha256:abc\n")
    upstream.write_text('[upstream]\nversion_key = "UPSTREAM_APP_VERSION"\n')

    assert read_upstream_version(dockerfile, upstream) == "v1.2.3"  # nosec B101


def test_aio_next_version_uses_upstream_version_and_revision_tags(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "Dockerfile").write_text("ARG UPSTREAM_VERSION=v2.0.0\n")
    (tmp_path / "upstream.toml").write_text("[upstream]\n")
    _commit(tmp_path, "feat(test): initial")
    _git(tmp_path, "tag", "v2.0.0-aio.1")

    assert (  # nosec B101
        next_aio_release_version(
            tmp_path, tmp_path / "Dockerfile", tmp_path / "upstream.toml"
        )
        == "v2.0.0-aio.2"
    )


def test_semver_next_version_uses_conventional_commit_bump(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "README.md").write_text("initial\n")
    _commit(tmp_path, "chore(test): initial")
    _git(tmp_path, "tag", "v1.2.3")
    (tmp_path / "README.md").write_text("feature\n")
    _commit(tmp_path, "feat(test): add feature")

    assert next_semver_release_version(tmp_path) == "v1.3.0"  # nosec B101


def test_latest_changelog_version_supports_linked_headings(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## [v1.2.3-aio.1](https://example.invalid)\n\n- test\n")

    assert latest_changelog_version(changelog) == "v1.2.3-aio.1"  # nosec B101


def test_release_publish_target_allows_changelog_format_followup(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("## Unreleased\n\n- initial\n")
    _commit(tmp_path, "feat(test): initial")
    (tmp_path / "CHANGELOG.md").write_text(
        "## v1.0.0-aio.1 - 2026-05-10\n\n- initial\n"
    )
    _commit(tmp_path, "chore(release): v1.0.0-aio.1")
    release_commit = _git_output(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "CHANGELOG.md").write_text(
        "## v1.0.0-aio.1 - 2026-05-10\n\n- initial\n\n"
    )
    _commit(tmp_path, "chore(release): format test changelog")
    publish_commit = _git_output(tmp_path, "rev-parse", "HEAD")

    assert release_commit != publish_commit  # nosec B101
    assert (  # nosec B101
        find_release_publish_target_commit(tmp_path, "v1.0.0-aio.1") == publish_commit
    )


def test_release_publish_target_rejects_arbitrary_post_release_commit(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("## Unreleased\n\n- initial\n")
    _commit(tmp_path, "feat(test): initial")
    (tmp_path / "CHANGELOG.md").write_text(
        "## v1.0.0-aio.1 - 2026-05-10\n\n- initial\n"
    )
    _commit(tmp_path, "chore(release): v1.0.0-aio.1")
    release_commit = _git_output(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "README.md").write_text("later\n")
    _commit(tmp_path, "fix(runtime): later change")

    assert (  # nosec B101
        find_release_publish_target_commit(tmp_path, "v1.0.0-aio.1") == release_commit
    )


def test_release_cli_supports_component_suffix(tmp_path: Path, capsys) -> None:
    _init_repo(tmp_path)
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "agent.Dockerfile").write_text(
        "ARG UPSTREAM_AGENT_VERSION=1.0.0\n"
    )
    (tmp_path / "components" / "agent.toml").write_text(
        '[upstream]\nversion_key = "UPSTREAM_AGENT_VERSION"\n'
    )
    (tmp_path / "components.toml").write_text("""
[components.agent]
dockerfile = "components/agent.Dockerfile"
upstream_config = "components/agent.toml"
release_suffix = "agent"
""")
    _commit(tmp_path, "feat(test): initial")

    assert (  # nosec B101
        main(["--repo-path", str(tmp_path), "--component", "agent", "next-version"])
        == 0
    )
    assert capsys.readouterr().out.strip() == "1.0.0-agent.1"  # nosec B101
