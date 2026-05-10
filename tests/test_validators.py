from __future__ import annotations

from pathlib import Path

from aio_fleet.manifest import RepoConfig
from aio_fleet.validators import (
    catalog_quality_findings,
    catalog_repo_failures,
    derived_repo_failures,
    pinned_action_failures,
    publish_platform_failures,
    repo_local_workflow_failures,
    runtime_contract_failures,
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
        "public": True,
        "catalog_assets": [{"source": "example-aio.xml", "target": "example-aio.xml"}],
    }
    raw.update(overrides)
    return RepoConfig(name="example-aio", raw=raw, defaults={}, owner="JSONbored")


def _write_minimal_derived_repo(tmp_path: Path) -> None:
    for path in [
        "Dockerfile",
        "README.md",
        "pyproject.toml",
        ".aio-fleet.yml",
        "tests/integration/test_container_runtime.py",
        "example-aio.xml",
    ]:
        file_path = tmp_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("ok\n")


def _write_catalog_readme(
    catalog_path: Path,
    *,
    available: list[str] | None = None,
    in_progress: list[str] | None = None,
    candidates: list[str] | None = None,
    available_count: int | None = None,
    star_repos: list[str] | None = None,
    star_type: str = "Date",
) -> None:
    available = ["example-aio"] if available is None else available
    in_progress = [] if in_progress is None else in_progress
    candidates = [] if candidates is None else candidates
    count = len(available) if available_count is None else available_count
    star_repos = star_repos or ["JSONbored/awesome-unraid", "JSONbored/example-aio"]
    image_query = f"repos={','.join(star_repos)}&type={star_type}&theme=dark"
    link_fragment = "&".join(star_repos + ["Date"])
    lines = [
        "# Awesome Unraid",
        "",
        f"- Available templates: `{count}`",
        "",
        f"### Available Templates ({count})",
        "",
        *[
            f"- **[{name}](https://github.com/JSONbored/{name})** - Ready."
            for name in available
        ],
        "",
        f"### In Progress ({len(in_progress)})",
        "",
        *[
            f"- **[{name}](https://github.com/JSONbored/{name})** - In progress."
            for name in in_progress
        ],
        "",
        "### Upcoming Candidates",
        "",
        *[f"- **{name}** - Candidate." for name in candidates],
        "",
        "## Star History",
        "",
        (
            "[![Star History Chart]"
            f"(https://api.star-history.com/svg?{image_query})]"
            f"(https://star-history.com/#{link_fragment})"
        ),
        "",
    ]
    (catalog_path / "README.md").write_text("\n".join(lines))


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


def test_pinned_action_validation_covers_yaml_and_action_yaml(tmp_path: Path) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    action_dir = tmp_path / ".github" / "actions" / "local"
    workflow_dir.mkdir(parents=True)
    action_dir.mkdir(parents=True)
    (workflow_dir / "build.yaml").write_text("""
jobs:
  test:
    steps:
      - uses: actions/checkout@v6
""")
    (action_dir / "action.yaml").write_text("""
runs:
  using: composite
  steps:
    - uses: actions/setup-python@v6
""")

    assert pinned_action_failures(tmp_path) == [  # nosec B101
        ".github/actions/local/action.yaml: action is not pinned to a full SHA -> actions/setup-python@v6",
        ".github/workflows/build.yaml: action is not pinned to a full SHA -> actions/checkout@v6",
    ]


def test_app_repo_local_workflows_are_rejected(tmp_path: Path) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "pwn.yml").write_text("name: pwn\n")

    assert repo_local_workflow_failures(_repo(tmp_path)) == [  # nosec B101
        "example-aio: .github/workflows is disabled; app repos are checked by aio-fleet / required"
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

    assert derived_repo_failures(tmp_path) == [
        "missing required file: agent.xml"
    ]  # nosec B101


def test_derived_repo_validation_rejects_escaped_component_template(
    tmp_path: Path,
) -> None:
    _write_minimal_derived_repo(tmp_path)
    (tmp_path / "components.toml").write_text("""
[components.agent]
template = "../agent.xml"
""")

    assert derived_repo_failures(tmp_path) == [
        "repo path escapes checkout: ../agent.xml"
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
  <Repository>ghcr.io/jsonbored/example-aio:latest</Repository>
  <Registry>https://ghcr.io/jsonbored/example-aio</Registry>
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
    assert (  # nosec B101
        "example-aio: example-aio.xml <Repository> must use Docker Hub shorthand, got ghcr.io/jsonbored/example-aio:latest"
        in failures
    )
    assert (  # nosec B101
        "example-aio: example-aio.xml <Registry> must point at Docker Hub, got https://ghcr.io/jsonbored/example-aio"
        in failures
    )


def test_template_metadata_validation_rejects_untrusted_repository_namespace(
    tmp_path: Path,
) -> None:
    (tmp_path / "example-aio.xml").write_text("""<?xml version="1.0"?>
<Container version="2">
  <Name>example-aio</Name>
  <Repository>attacker/example-aio:latest</Repository>
  <Registry>https://hub.docker.com/r/attacker/example-aio</Registry>
  <Project>https://github.com/JSONbored/example-aio</Project>
  <Support>https://github.com/JSONbored/example-aio/issues</Support>
  <Overview>Example.</Overview>
  <Category>Tools:</Category>
  <TemplateURL>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/example-aio.xml</TemplateURL>
  <Icon>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/icons/example.png</Icon>
</Container>
""")

    failures = template_metadata_failures(_repo(tmp_path), _Manifest())  # type: ignore[arg-type]

    assert (  # nosec B101
        "example-aio: example-aio.xml <Repository> must use one of ['jsonbored/example-aio'], got attacker/example-aio:latest"
        in failures
    )


def test_template_metadata_validation_handles_non_file_xml(tmp_path: Path) -> None:
    (tmp_path / "example-aio.xml").mkdir()

    failures = template_metadata_failures(_repo(tmp_path), _Manifest())  # type: ignore[arg-type]

    assert failures == [  # nosec B101
        "example-aio: catalog XML example-aio.xml must be a file"
    ]


def test_template_metadata_validation_rejects_defused_xml(tmp_path: Path) -> None:
    (tmp_path / "example-aio.xml").write_text("""<?xml version="1.0"?>
<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
<Container version="2"><Name>&xxe;</Name></Container>
""")

    failures = template_metadata_failures(_repo(tmp_path), _Manifest())  # type: ignore[arg-type]

    assert len(failures) == 1  # nosec B101
    assert failures[0].startswith(  # nosec B101
        "example-aio: unable to parse catalog XML example-aio.xml:"
    )


def test_template_metadata_validation_rejects_nested_options_and_bad_changes(
    tmp_path: Path,
) -> None:
    (tmp_path / "example-aio.xml").write_text("""<?xml version="1.0"?>
<Container version="2">
  <Name>example-aio</Name>
  <Repository>jsonbored/example-aio:latest</Repository>
  <Registry>https://hub.docker.com/r/jsonbored/example-aio</Registry>
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


def test_template_metadata_validation_applies_manifest_declared_targets(
    tmp_path: Path,
) -> None:
    (tmp_path / "example-aio.xml").write_text("""<?xml version="1.0"?>
<Container version="2">
  <Name>example-aio</Name>
  <Repository>jsonbored/example-aio:latest</Repository>
  <Registry>https://hub.docker.com/r/jsonbored/example-aio</Registry>
  <Project>https://github.com/JSONbored/example-aio</Project>
  <Support>https://github.com/JSONbored/example-aio/issues</Support>
  <Overview>Example.</Overview>
  <Category>Tools:</Category>
  <TemplateURL>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/example-aio.xml</TemplateURL>
  <Icon>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/icons/example.png</Icon>
  <Changes>### 2026-05-01</Changes>
  <Config Name="Present" Target="PRESENT"/>
</Container>
""")

    failures = template_metadata_failures(
        _repo(
            tmp_path,
            validation={
                "required_targets": ["PRESENT", "MISSING"],
                "forbidden_targets": ["PRESENT"],
            },
        ),
        _Manifest(),  # type: ignore[arg-type]
    )

    assert any("MISSING" in failure for failure in failures)  # nosec B101
    assert any("manifest-forbidden" in failure for failure in failures)  # nosec B101


def test_runtime_contract_allows_unpublished_optional_port(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text(
        "FROM ubuntu@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        'ENTRYPOINT ["/init"]\n'
        "S6_CMD_WAIT_FOR_SERVICES_MAXTIME=300000\n"
        "S6_BEHAVIOUR_IF_STAGE2_FAILS=2\n"
        'VOLUME ["/config"]\n'
        "HEALTHCHECK CMD curl -fsS http://localhost:3000/ || exit 1\n"
        "EXPOSE 3000\n"
    )
    (tmp_path / "example-aio.xml").write_text("""<?xml version="1.0"?>
<Container version="2">
  <Name>example-aio</Name>
  <Repository>jsonbored/example-aio:latest</Repository>
  <Registry>https://hub.docker.com/r/jsonbored/example-aio</Registry>
  <Project>https://github.com/JSONbored/example-aio</Project>
  <Support>https://github.com/JSONbored/example-aio/issues</Support>
  <Overview>Example.</Overview>
  <Category>Tools:</Category>
  <TemplateURL>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/example-aio.xml</TemplateURL>
  <Icon>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/icons/example.png</Icon>
  <Config Name="Web UI Port" Target="3000" Type="Port" Required="true">3000</Config>
  <Config Name="Optional API Port" Target="8765" Type="Port" Required="false" Default=""></Config>
</Container>
""")

    failures = runtime_contract_failures(_repo(tmp_path))

    assert not any(
        "8765 is not exposed" in failure for failure in failures
    )  # nosec B101


def test_template_metadata_validation_rejects_common_quality_drift(
    tmp_path: Path,
) -> None:
    (tmp_path / "example-aio.xml").write_text("""<?xml version="1.0"?>
<Container version="2">
  <Name>Example-AIO</Name>
  <Repository>jsonbored/example-aio:latest</Repository>
  <Registry>https://hub.docker.com/r/jsonbored/example-aio</Registry>
  <Project>not-a-url</Project>
  <Support>https://github.com/JSONbored/example-aio/issues</Support>
  <Overview>Example overview with defaults and advanced settings.</Overview>
  <Category>FakeCategory</Category>
  <TemplateURL>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/example-aio.xml</TemplateURL>
  <Icon>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/icons/example.png</Icon>
  <DonateText>Support JSONbored</DonateText>
  <DonateLink/>
  <Changes>### 2026-01-01</Changes>
</Container>
""")

    failures = template_metadata_failures(_repo(tmp_path), _Manifest())  # type: ignore[arg-type]

    assert (
        "example-aio: example-aio.xml <Name> must be lowercase" in failures
    )  # nosec B101
    assert (
        "example-aio: example-aio.xml <Project> must be an HTTP(S) URL" in failures
    )  # nosec B101
    assert (  # nosec B101
        "example-aio: example-aio.xml <Category> token has unknown root: FakeCategory"
        in failures
    )
    assert (  # nosec B101
        "example-aio: example-aio.xml <DonateText> and <DonateLink> must be both blank or both populated"
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
    _write_catalog_readme(catalog_path)
    (catalog_path / "example-aio.xml").write_text("""<?xml version="1.0"?>
<Container version="2">
  <Name>example-aio</Name>
  <Repository>jsonbored/example-aio:latest</Repository>
  <Registry>https://hub.docker.com/r/jsonbored/example-aio</Registry>
  <Project>https://github.com/JSONbored/example-aio</Project>
  <Support>https://github.com/JSONbored/example-aio/issues</Support>
  <Overview>Example.</Overview>
  <Category>Tools:</Category>
  <TemplateURL>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/example-aio.xml</TemplateURL>
  <Icon>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/icons/example.png</Icon>
</Container>
""")

    assert catalog_repo_failures(manifest, catalog_path) == []  # type: ignore[arg-type] # nosec B101


def test_catalog_validation_reports_readme_template_drift(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "missing-repo")
    manifest = _Manifest()
    manifest.repos = {"example-aio": repo}
    catalog_path = tmp_path / "catalog"
    (catalog_path / "icons").mkdir(parents=True)
    (catalog_path / "icons" / "example.png").write_bytes(b"icon")
    _write_catalog_readme(
        catalog_path,
        available=[],
        candidates=["example-aio"],
        available_count=0,
        star_repos=["JSONbored/awesome-unraid", "JSONbored/example-aio"],
    )
    (catalog_path / "example-aio.xml").write_text("""<?xml version="1.0"?>
<Container version="2">
  <Name>example-aio</Name>
  <Repository>jsonbored/example-aio:latest</Repository>
  <Registry>https://hub.docker.com/r/jsonbored/example-aio</Registry>
  <Project>https://github.com/JSONbored/example-aio</Project>
  <Support>https://github.com/JSONbored/example-aio/issues</Support>
  <Overview>Example.</Overview>
  <Category>Tools:</Category>
  <TemplateURL>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/example-aio.xml</TemplateURL>
  <Icon>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/icons/example.png</Icon>
</Container>
""")

    failures = catalog_repo_failures(manifest, catalog_path)  # type: ignore[arg-type]

    assert any(
        "available template count must be 1, got 0" in failure for failure in failures
    )  # nosec B101
    assert any(
        "Available Templates missing published template(s): example-aio" in failure
        for failure in failures
    )  # nosec B101
    assert any(
        "Upcoming Candidates lists published template(s): example-aio" in failure
        for failure in failures
    )  # nosec B101


def test_catalog_validation_reports_star_history_drift(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "missing-repo")
    manifest = _Manifest()
    manifest.repos = {"example-aio": repo}
    catalog_path = tmp_path / "catalog"
    (catalog_path / "icons").mkdir(parents=True)
    (catalog_path / "icons" / "example.png").write_bytes(b"icon")
    _write_catalog_readme(
        catalog_path,
        star_repos=["JSONbored/awesome-unraid", "JSONbored/nanoclaw-aio"],
        star_type="",
    )
    (catalog_path / "example-aio.xml").write_text("""<?xml version="1.0"?>
<Container version="2">
  <Name>example-aio</Name>
  <Repository>jsonbored/example-aio:latest</Repository>
  <Registry>https://hub.docker.com/r/jsonbored/example-aio</Registry>
  <Project>https://github.com/JSONbored/example-aio</Project>
  <Support>https://github.com/JSONbored/example-aio/issues</Support>
  <Overview>Example.</Overview>
  <Category>Tools:</Category>
  <TemplateURL>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/example-aio.xml</TemplateURL>
  <Icon>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/icons/example.png</Icon>
</Container>
""")

    failures = catalog_repo_failures(manifest, catalog_path)  # type: ignore[arg-type]

    assert any(
        "Star History image missing repo(s): jsonbored/example-aio" in failure
        for failure in failures
    )  # nosec B101
    assert any(
        "Star History image includes unpublished repo(s): jsonbored/nanoclaw-aio"
        in failure
        for failure in failures
    )  # nosec B101
    assert any(
        "Star History image URL must set type=Date" in failure for failure in failures
    )  # nosec B101


def test_catalog_quality_audit_reports_catalog_presentation_drift(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    manifest = _Manifest()
    manifest.repos = {"example-aio": repo}
    catalog_path = tmp_path / "catalog"
    (catalog_path / "icons").mkdir(parents=True)
    (catalog_path / "icons" / "example.png").write_bytes(b"not-png")
    (catalog_path / "example-aio.xml").write_text("""<?xml version="1.0"?>
<Container version="2">
  <Name>Example-AIO</Name>
  <Repository>jsonbored/example-aio:latest</Repository>
  <Registry>https://hub.docker.com/r/jsonbored/example-aio</Registry>
  <Project>https://github.com/JSONbored/example-aio</Project>
  <Support>https://github.com/JSONbored/example-aio/issues</Support>
  <Overview>Too short.</Overview>
  <Category>Tools:</Category>
  <TemplateURL>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/example-aio.xml</TemplateURL>
  <Icon>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/icons/example.png</Icon>
  <DonateText/>
  <DonateLink/>
  <Changes>### 2026-01-01</Changes>
</Container>
""")

    findings = catalog_quality_findings(manifest, catalog_path)  # type: ignore[arg-type]

    assert any(
        "<Name> must be lowercase" in finding for finding in findings
    )  # nosec B101
    assert any(
        "fuller CA-facing setup guidance" in finding for finding in findings
    )  # nosec B101
    assert any("not a valid PNG" in finding for finding in findings)  # nosec B101
