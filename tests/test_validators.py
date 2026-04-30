from __future__ import annotations

from pathlib import Path

from aio_fleet.manifest import RepoConfig
from aio_fleet.validators import (
    catalog_repo_failures,
    derived_repo_failures,
    pinned_action_failures,
    publish_platform_failures,
    template_metadata_failures,
    tracked_artifact_failures,
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


def _write_minimal_derived_repo(tmp_path: Path) -> None:
    for path in [
        "Dockerfile",
        "README.md",
        "pyproject.toml",
        "tests/template/test_validate_template.py",
        "tests/integration/test_container_runtime.py",
        "scripts/validate-template.py",
        "scripts/update-template-changes.py",
        ".github/FUNDING.yml",
        "SECURITY.md",
        ".github/pull_request_template.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/installation_help.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        "renovate.json",
        "example-aio.xml",
    ]:
        file_path = tmp_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("ok\n")


def test_pinned_action_validation_rejects_tagged_actions(tmp_path: Path) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "build.yml").write_text("""
jobs:
  test:
    steps:
      - uses: actions/checkout@v6
""")

    assert pinned_action_failures(tmp_path) == [  # nosec B101
        ".github/workflows/build.yml: action is not pinned to a full SHA -> actions/checkout@v6"
    ]


def test_derived_repo_validation_accepts_minimal_repo(tmp_path: Path) -> None:
    _write_minimal_derived_repo(tmp_path)

    assert derived_repo_failures(tmp_path) == []  # nosec B101


def test_derived_repo_validation_rejects_template_leftovers(tmp_path: Path) -> None:
    repo_path = tmp_path / "example-aio"
    repo_path.mkdir()
    _write_minimal_derived_repo(repo_path)
    (repo_path / "template-aio.xml").write_text("leftover\n")

    assert derived_repo_failures(repo_path) == [  # nosec B101
        "remove template placeholder path in derived repo: template-aio.xml"
    ]


def test_derived_repo_validation_loads_component_templates(tmp_path: Path) -> None:
    _write_minimal_derived_repo(tmp_path)
    (tmp_path / "components.toml").write_text("""
[components.agent]
template = "agent.xml"
""")
    (tmp_path / "scripts" / "components.py").write_text("ok\n")

    assert derived_repo_failures(tmp_path) == [
        "missing required file: agent.xml"
    ]  # nosec B101


def test_publish_platform_validation_rejects_unhandled_arm64(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("""
ARG TARGETARCH
RUN case "${TARGETARCH}" in amd64) echo ok ;; *) exit 1 ;; esac
""")

    failures = publish_platform_failures(_repo(tmp_path))

    assert failures == [  # nosec B101
        "example-aio: Dockerfile does not appear to handle arm64 but publish_platforms includes linux/arm64"
    ]


def test_template_metadata_validation_checks_catalog_urls(tmp_path: Path) -> None:
    (tmp_path / "example-aio.xml").write_text("""<?xml version="1.0"?>
<Container version="2">
  <Name>example-aio</Name>
  <Project>https://github.com/JSONbored/example-aio</Project>
  <Support>https://github.com/JSONbored/example-aio/issues</Support>
  <Overview>Example.</Overview>
  <Category>Tools:</Category>
  <TemplateURL>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/wrong.xml</TemplateURL>
  <Icon>https://example.com/icon.png</Icon>
</Container>
""")

    failures = template_metadata_failures(_repo(tmp_path), _Manifest())  # type: ignore[arg-type]

    assert (  # nosec B101
        "example-aio: example-aio.xml TemplateURL must be "
        "https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/example-aio.xml, got "
        "https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/wrong.xml"
    ) in failures
    assert (  # nosec B101
        "example-aio: example-aio.xml Icon must point at JSONbored/awesome-unraid/main/icons/"
    ) in failures


def test_template_metadata_validation_rejects_nested_options_and_bad_changes(
    tmp_path: Path,
) -> None:
    (tmp_path / "example-aio.xml").write_text("""<?xml version="1.0"?>
<Container version="2">
  <Name>example-aio</Name>
  <Project>https://github.com/JSONbored/example-aio</Project>
  <Support>https://github.com/JSONbored/example-aio/issues</Support>
  <Overview>Example.</Overview>
  <Category>Tools:</Category>
  <TemplateURL>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/example-aio.xml</TemplateURL>
  <Icon>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/icons/example.png</Icon>
  <Changes>bad heading</Changes>
  <Config Name="Mode" Target="MODE"><Option>one</Option></Config>
</Container>
""")

    failures = template_metadata_failures(_repo(tmp_path), _Manifest())  # type: ignore[arg-type]

    assert (  # nosec B101
        "example-aio: example-aio.xml <Changes> must start with '### YYYY-MM-DD'"
        in failures
    )
    assert (  # nosec B101
        "example-aio: example-aio.xml Config Mode uses nested <Option> tags; use pipe-delimited values instead"
        in failures
    )


def test_tracked_artifact_validation_rejects_tfstate_and_pycache(
    tmp_path: Path,
) -> None:
    import subprocess  # nosec B404

    subprocess.run(
        ["git", "init"], cwd=tmp_path, check=True, capture_output=True
    )  # nosec
    (tmp_path / "infra" / "github").mkdir(parents=True)
    (tmp_path / "infra" / "github" / "terraform.tfstate").write_text("{}\n")
    (tmp_path / "tests" / "__pycache__").mkdir(parents=True)
    (tmp_path / "tests" / "__pycache__" / "test.pyc").write_bytes(b"cache")
    subprocess.run(  # nosec
        [
            "git",
            "add",
            "-f",
            "infra/github/terraform.tfstate",
            "tests/__pycache__/test.pyc",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    failures = tracked_artifact_failures(tmp_path)

    assert any("terraform.tfstate" in failure for failure in failures)  # nosec B101
    assert any("test.pyc" in failure for failure in failures)  # nosec B101


def test_catalog_validation_skips_unpublished_repos(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo", catalog_published=False)
    manifest = _Manifest()
    manifest.repos = {"example-aio": repo}

    assert catalog_repo_failures(manifest, tmp_path / "catalog") == [  # type: ignore[arg-type] # nosec B101
        f"catalog path missing: {tmp_path / 'catalog'}"
    ]

    (tmp_path / "catalog").mkdir()
    assert catalog_repo_failures(manifest, tmp_path / "catalog") == []  # type: ignore[arg-type] # nosec B101

    (tmp_path / "catalog" / "example-aio.xml").write_text("<Container />\n")
    assert catalog_repo_failures(manifest, tmp_path / "catalog") == [  # type: ignore[arg-type] # nosec B101
        "example-aio: catalog target exists while catalog_published is false: example-aio.xml"
    ]


def test_catalog_validation_allows_catalog_only_ci_without_source_checkout(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "missing-repo")
    manifest = _Manifest()
    manifest.repos = {"example-aio": repo}
    catalog_path = tmp_path / "catalog"
    (catalog_path / "icons").mkdir(parents=True)
    (catalog_path / "icons" / "example.png").write_bytes(b"icon")
    (catalog_path / "example-aio.xml").write_text("""<?xml version="1.0"?>
<Container version="2">
  <Name>example-aio</Name>
  <Project>https://github.com/JSONbored/example-aio</Project>
  <Support>https://github.com/JSONbored/example-aio/issues</Support>
  <Overview>Example.</Overview>
  <Category>Tools:</Category>
  <TemplateURL>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/example-aio.xml</TemplateURL>
  <Icon>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/icons/example.png</Icon>
</Container>
""")

    assert catalog_repo_failures(manifest, catalog_path) == []  # type: ignore[arg-type] # nosec B101
