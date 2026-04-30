from __future__ import annotations

from pathlib import Path

from aio_fleet.manifest import RepoConfig
from aio_fleet.validators import (
    catalog_repo_failures,
    pinned_action_failures,
    publish_platform_failures,
    template_metadata_failures,
)


class _Manifest:
    raw = {"awesome_unraid_repository": "JSONbored/awesome-unraid"}
    repos: dict[str, RepoConfig] = {}


def _repo(tmp_path: Path, **overrides: object) -> RepoConfig:
    raw = {
        "path": str(tmp_path),
        "app_slug": "example-aio",
        "image_name": "jsonbored/example-aio",
        "docker_cache_scope": "example-aio-image",
        "pytest_image_tag": "example-aio:pytest",
        "publish_profile": "changelog-version",
        "publish_platforms": "linux/amd64,linux/arm64",
        "catalog_assets": [{"source": "example-aio.xml", "target": "example-aio.xml"}],
    }
    raw.update(overrides)
    return RepoConfig(name="example-aio", raw=raw, defaults={}, owner="JSONbored")


def test_pinned_action_validation_rejects_tagged_actions(tmp_path: Path) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build.yml").write_text(
        """
jobs:
  test:
    steps:
      - uses: actions/checkout@v6
"""
    )

    assert pinned_action_failures(tmp_path) == [  # nosec B101
        ".github/workflows/build.yml: action is not pinned to a full SHA -> actions/checkout@v6"
    ]


def test_publish_platform_validation_rejects_unhandled_arm64(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text(
        """
ARG TARGETARCH
RUN case "${TARGETARCH}" in amd64) echo ok ;; *) exit 1 ;; esac
"""
    )

    failures = publish_platform_failures(_repo(tmp_path))

    assert failures == [  # nosec B101
        "example-aio: Dockerfile does not appear to handle arm64 but publish_platforms includes linux/arm64"
    ]


def test_template_metadata_validation_checks_catalog_urls(tmp_path: Path) -> None:
    (tmp_path / "example-aio.xml").write_text(
        """<?xml version="1.0"?>
<Container version="2">
  <Name>example-aio</Name>
  <Project>https://github.com/JSONbored/example-aio</Project>
  <Support>https://github.com/JSONbored/example-aio/issues</Support>
  <Overview>Example.</Overview>
  <Category>Tools:</Category>
  <TemplateURL>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/wrong.xml</TemplateURL>
  <Icon>https://example.com/icon.png</Icon>
</Container>
"""
    )

    failures = template_metadata_failures(_repo(tmp_path), _Manifest())  # type: ignore[arg-type]

    assert (  # nosec B101
        "example-aio: example-aio.xml TemplateURL must be "
        "https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/example-aio.xml, got "
        "https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/wrong.xml"
    ) in failures
    assert (  # nosec B101
        "example-aio: example-aio.xml Icon must point at JSONbored/awesome-unraid/main/icons/"
    ) in failures


def test_catalog_validation_skips_unpublished_repos(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo", catalog_published=False)
    manifest = _Manifest()
    manifest.repos = {"example-aio": repo}

    assert catalog_repo_failures(manifest, tmp_path / "catalog") == [  # type: ignore[arg-type] # nosec B101
        f"catalog path missing: {tmp_path / 'catalog'}"
    ]

    (tmp_path / "catalog").mkdir()
    assert catalog_repo_failures(manifest, tmp_path / "catalog") == []  # type: ignore[arg-type] # nosec B101
