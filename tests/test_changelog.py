from __future__ import annotations

import subprocess  # nosec B404
from pathlib import Path

from aio_fleet.changelog import build_release_plan
from aio_fleet.manifest import RepoConfig


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)  # nosec


def _init_repo(repo: Path) -> None:
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Tests")
    _git(repo, "config", "commit.gpgsign", "false")


def test_component_release_plan_uses_component_xml_and_suffix(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "components" / "agent").mkdir(parents=True)
    (tmp_path / "Dockerfile").write_text("ARG UPSTREAM_SIGNOZ_VERSION=v0.121.1\n")
    (tmp_path / "components" / "agent" / "Dockerfile").write_text(
        "ARG UPSTREAM_OTELCOL_VERSION=0.151.0\n"
    )
    (tmp_path / "signoz-aio.xml").write_text(
        "<Container><Changes>old</Changes></Container>"
    )
    (tmp_path / "signoz-agent.xml").write_text(
        "<Container><Changes>old</Changes></Container>"
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "feat(test): initial")

    repo = RepoConfig(
        name="signoz-aio",
        raw={
            "path": str(tmp_path),
            "app_slug": "signoz-aio",
            "image_name": "jsonbored/signoz-aio",
            "docker_cache_scope": "signoz-aio-image",
            "pytest_image_tag": "signoz-aio:pytest",
            "publish_profile": "signoz-suite",
            "upstream_version_key": "UPSTREAM_SIGNOZ_VERSION",
            "catalog_assets": [
                {"source": "signoz-aio.xml", "target": "signoz-aio.xml"},
                {"source": "signoz-agent.xml", "target": "signoz-agent.xml"},
            ],
            "components": {
                "aio": {
                    "dockerfile": "Dockerfile",
                    "xml_paths": ["signoz-aio.xml"],
                    "upstream_version_key": "UPSTREAM_SIGNOZ_VERSION",
                    "release_suffix": "aio",
                },
                "agent": {
                    "dockerfile": "components/agent/Dockerfile",
                    "xml_paths": ["signoz-agent.xml"],
                    "upstream_version_key": "UPSTREAM_OTELCOL_VERSION",
                    "release_suffix": "agent",
                },
            },
        },
        defaults={},
        owner="JSONbored",
    )

    aio = build_release_plan(repo, component="aio")
    agent = build_release_plan(repo, component="agent")

    assert aio.version == "v0.121.1-aio.1"  # nosec B101
    assert aio.xml_paths == [tmp_path / "signoz-aio.xml"]  # nosec B101
    assert agent.version == "0.151.0-agent.1"  # nosec B101
    assert agent.xml_paths == [tmp_path / "signoz-agent.xml"]  # nosec B101
