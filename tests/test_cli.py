from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

from aio_fleet import cli
from aio_fleet.cli import (
    _repo_python,
    cmd_check_run,
    cmd_debt_report,
    cmd_export_app_manifest,
    cmd_infra_doctor,
    cmd_onboard_repo,
    cmd_poll,
    cmd_trunk_audit,
    cmd_validate_template_common,
)
from aio_fleet.manifest import load_manifest
from aio_fleet.poll import PollTarget
from aio_fleet.workflows import rendered_workflows

OLD_REF = "1" * 40
NEW_REF = "2" * 40


def test_repo_python_prefers_repo_virtualenv(tmp_path: Path) -> None:
    repo_python = tmp_path / ".venv" / "bin" / "python"
    repo_python.parent.mkdir(parents=True)
    repo_python.write_text("#!/usr/bin/env sh\n")
    repo_python.chmod(0o755)

    assert _repo_python(tmp_path) == str(repo_python)  # nosec B101


def test_repo_python_falls_back_to_current_interpreter(tmp_path: Path) -> None:
    assert _repo_python(tmp_path) == sys.executable  # nosec B101


def test_trunk_audit_summarizes_repo_results(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo_path = tmp_path / "repo"
    (repo_path / ".trunk").mkdir(parents=True)
    (repo_path / ".trunk" / "trunk.yaml").write_text("version: 0.1\n")
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
repos:
  example-aio:
    path: {repo_path}
    app_slug: example-aio
    image_name: jsonbored/example-aio
    docker_cache_scope: example-aio-image
    pytest_image_tag: example-aio:pytest
""")

    def fake_run(command: list[str], cwd: Path | None = None) -> SimpleNamespace:
        assert command[:2] == ["trunk", "check"]  # nosec B101
        assert cwd == repo_path  # nosec B101
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli, "_run", fake_run)

    result = cmd_trunk_audit(
        Namespace(manifest=str(manifest), repo=None, verbose=False)
    )

    assert result == 0  # nosec B101
    assert "example-aio: trunk=ok" in capsys.readouterr().out  # nosec B101


def test_debt_report_outputs_json_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
repos:
  example-aio:
    path: {repo_path}
    app_slug: example-aio
    image_name: jsonbored/example-aio
    docker_cache_scope: example-aio-image
    pytest_image_tag: example-aio:pytest
""")
    boilerplate = tmp_path / "boilerplate.yml"
    boilerplate.write_text("profiles:\n  aio:\n    files: []\n")

    def fake_run(command: list[str], cwd: Path | None = None) -> SimpleNamespace:
        if command[:2] == ["git", "status"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:2] == ["git", "rev-list"]:
            return SimpleNamespace(returncode=0, stdout="0 0\n", stderr="")
        if command[:2] == ["git", "ls-files"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(cli, "_run", fake_run)

    result = cmd_debt_report(
        Namespace(
            manifest=str(manifest),
            catalog_path=None,
            github=False,
            policy="unused.yml",
            boilerplate_config=str(boilerplate),
            ref="0" * 40,
            trunk=False,
            format="json",
        )
    )

    assert result == 0  # nosec B101
    assert '"repos": 1' in capsys.readouterr().out  # nosec B101


def test_check_run_dry_run_outputs_payload(tmp_path: Path, capsys) -> None:
    manifest, _repo_path = _write_minimal_manifest(tmp_path)

    result = cmd_check_run(
        Namespace(
            manifest=str(manifest),
            repo="example-aio",
            sha="e" * 40,
            event="pull_request",
            status="completed",
            conclusion=None,
            summary="central validation passed",
            details_url=None,
            dry_run=True,
        )
    )

    assert result == 0  # nosec B101
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "aio-fleet / required"  # nosec B101
    assert payload["conclusion"] == "success"  # nosec B101


def test_poll_missing_checks_only_skips_satisfied_targets(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manifest, _repo_path = _write_minimal_manifest(tmp_path)
    repo = load_manifest(manifest).repo("example-aio")

    monkeypatch.setattr(
        cli,
        "poll_targets",
        lambda *args, **kwargs: [
            PollTarget(
                repo=repo,
                sha="f" * 40,
                event="pull_request",
                source="pr:1",
            )
        ],
    )
    monkeypatch.setattr(cli, "check_run_satisfied", lambda *args, **kwargs: True)

    result = cmd_poll(
        Namespace(
            manifest=str(manifest),
            no_prs=False,
            no_main=False,
            create_checks=False,
            missing_checks_only=True,
            dry_run=False,
            format="json",
        )
    )

    assert result == 0  # nosec B101
    assert json.loads(capsys.readouterr().out) == {"targets": []}  # nosec B101


def test_export_app_manifest_prints_future_app_manifest(tmp_path: Path, capsys) -> None:
    manifest, _repo_path = _write_minimal_manifest(tmp_path)

    result = cmd_export_app_manifest(
        Namespace(
            manifest=str(manifest),
            repo="example-aio",
            output=None,
            write=False,
        )
    )

    assert result == 0  # nosec B101
    output = capsys.readouterr().out
    assert "schema_version: 1" in output  # nosec B101
    assert "repo: example-aio" in output  # nosec B101


def _write_minimal_manifest(tmp_path: Path) -> tuple[Path, Path]:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
repos:
  example-aio:
    path: {repo_path}
    app_slug: example-aio
    image_name: jsonbored/example-aio
    docker_cache_scope: example-aio-image
    pytest_image_tag: example-aio:pytest
""")
    return manifest, repo_path


def _write_rendered_caller(manifest_path: Path, repo_name: str, ref: str) -> None:
    manifest = load_manifest(manifest_path)
    repo = manifest.repo(repo_name)
    for path, text in rendered_workflows(manifest, repo, ref).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_debt_report_uses_existing_caller_pin_by_default(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manifest, _repo_path = _write_minimal_manifest(tmp_path)
    _write_rendered_caller(manifest, "example-aio", OLD_REF)
    boilerplate = tmp_path / "boilerplate.yml"
    boilerplate.write_text("profiles:\n  aio:\n    files: []\n")

    def fake_run(command: list[str], cwd: Path | None = None) -> SimpleNamespace:
        if command[:2] == ["git", "status"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:2] == ["git", "rev-list"]:
            return SimpleNamespace(returncode=0, stdout="0 0\n", stderr="")
        if command[:2] == ["git", "ls-files"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(cli, "_current_ref", lambda: NEW_REF)
    monkeypatch.setattr(cli, "_run", fake_run)

    result = cmd_debt_report(
        Namespace(
            manifest=str(manifest),
            catalog_path=None,
            github=False,
            policy="unused.yml",
            boilerplate_config=str(boilerplate),
            ref=None,
            trunk=False,
            format="json",
        )
    )

    assert result == 0  # nosec B101
    report = json.loads(capsys.readouterr().out)
    assert report["ref"] == "caller-pins"  # nosec B101
    assert report["summary"]["workflow_drift"] == 0  # nosec B101


def test_sync_workflows_preserves_existing_caller_pin_by_default(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manifest, _repo_path = _write_minimal_manifest(tmp_path)
    _write_rendered_caller(manifest, "example-aio", OLD_REF)

    monkeypatch.setattr(cli, "_current_ref", lambda: NEW_REF)

    result = cli.cmd_sync_workflows(
        Namespace(
            manifest=str(manifest),
            repo=None,
            ref=None,
            dry_run=True,
            create_pr=False,
            branch="codex/aio-fleet-workflows",
            base="main",
            draft=False,
        )
    )

    assert result == 0  # nosec B101
    assert "workflow changes: 0" in capsys.readouterr().out  # nosec B101


def test_debt_report_text_prints_publish_state_once(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
repos:
  example-aio:
    path: {repo_path}
    app_slug: example-aio
    image_name: jsonbored/example-aio
    docker_cache_scope: example-aio-image
    pytest_image_tag: example-aio:pytest
""")
    boilerplate = tmp_path / "boilerplate.yml"
    boilerplate.write_text("profiles:\n  aio:\n    files: []\n")

    def fake_run(command: list[str], cwd: Path | None = None) -> SimpleNamespace:
        if command[:2] == ["git", "status"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:2] == ["git", "rev-list"]:
            return SimpleNamespace(returncode=0, stdout="0 0\n", stderr="")
        if command[:2] == ["git", "ls-files"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(cli, "_run", fake_run)

    result = cmd_debt_report(
        Namespace(
            manifest=str(manifest),
            catalog_path=None,
            github=False,
            policy="unused.yml",
            boilerplate_config=str(boilerplate),
            ref="0" * 40,
            trunk=False,
            format="text",
        )
    )

    assert result == 0  # nosec B101
    output = capsys.readouterr().out
    assert "publish=source-ready" in output  # nosec B101
    assert "publish=publish=" not in output  # nosec B101


def test_debt_report_treats_repos_missing_from_github_policy_as_manual(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
repos:
  unraid-aio-template:
    path: {repo_path}
    app_slug: unraid-aio-template
    image_name: jsonbored/unraid-aio-template
    docker_cache_scope: unraid-aio-template-image
    pytest_image_tag: aio-template:pytest
    publish_profile: template
""")
    boilerplate = tmp_path / "boilerplate.yml"
    boilerplate.write_text(
        "profiles:\n  aio:\n    files: []\n  template:\n    files: []\n"
    )
    policy = tmp_path / "github-policy.yml"
    policy.write_text("repositories: {}\n")

    def fake_run(command: list[str], cwd: Path | None = None) -> SimpleNamespace:
        if command[:2] == ["git", "status"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:2] == ["git", "rev-list"]:
            return SimpleNamespace(returncode=0, stdout="0 0\n", stderr="")
        if command[:2] == ["git", "ls-files"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:3] == ["gh", "pr", "list"]:
            return SimpleNamespace(returncode=0, stdout="0\n", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(cli, "_run", fake_run)

    result = cmd_debt_report(
        Namespace(
            manifest=str(manifest),
            catalog_path=None,
            github=True,
            policy=str(policy),
            boilerplate_config=str(boilerplate),
            ref="0" * 40,
            trunk=False,
            format="json",
        )
    )

    assert result == 0  # nosec B101
    report = json.loads(capsys.readouterr().out)
    assert report["repos"][0]["github_policy_failures"] == []  # nosec B101


def test_validate_template_common_accepts_manifest_repo(tmp_path: Path, capsys) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "example-aio.xml").write_text("""<?xml version="1.0"?>
<Container version="2">
  <Name>example-aio</Name>
  <Repository>jsonbored/example-aio:latest</Repository>
  <Registry>https://hub.docker.com/r/jsonbored/example-aio</Registry>
  <Project>https://github.com/JSONbored/example-aio</Project>
  <Support>https://github.com/JSONbored/example-aio/issues</Support>
  <Overview>Example defaults and advanced settings for operators.</Overview>
  <Category>Tools:</Category>
  <TemplateURL>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/example-aio.xml</TemplateURL>
  <Icon>https://raw.githubusercontent.com/JSONbored/awesome-unraid/main/icons/example.png</Icon>
  <DonateText/>
  <DonateLink/>
  <Changes>### 2026-01-01
- Generated from CHANGELOG.md during release preparation. Do not edit manually.
- Initial release.</Changes>
</Container>
""")
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
awesome_unraid_repository: JSONbored/awesome-unraid
repos:
  example-aio:
    path: {repo_path}
    app_slug: example-aio
    image_name: jsonbored/example-aio
    docker_cache_scope: example-aio-image
    pytest_image_tag: example-aio:pytest
    catalog_assets:
      - source: example-aio.xml
        target: example-aio.xml
""")

    result = cmd_validate_template_common(
        Namespace(manifest=str(manifest), repo="example-aio", repo_path=None, all=False)
    )

    assert result == 0  # nosec B101
    assert "common template validation passed" in capsys.readouterr().out  # nosec B101


def test_infra_doctor_checks_local_policy_without_tofu(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    git = shutil.which("git")
    assert git  # nosec B101
    subprocess.run(
        [git, "init"], cwd=tmp_path, check=True, capture_output=True
    )  # nosec B603
    (tmp_path / ".gitignore").write_text(
        "infra/github/*.tfstate\ninfra/github/*.tfstate.*\ninfra/github/*.tfvars\n"
    )
    infra = tmp_path / "infra" / "github"
    infra.mkdir(parents=True)
    (infra / ".terraform.lock.hcl").write_text("# lock\n")
    (infra / ".terraform").mkdir()
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
repos:
  example-aio:
    path: {tmp_path / "example-aio"}
    public: true
    app_slug: example-aio
    image_name: jsonbored/example-aio
    docker_cache_scope: example-aio-image
    pytest_image_tag: example-aio:pytest
""")
    policy = infra / "github-policy.yml"
    policy.write_text("""
owner: JSONbored
defaults:
  actions:
    patterns_allowed:
      - JSONbored/aio-fleet/.github/workflows/aio-*.yml@*
repositories:
  awesome-unraid: {}
  example-aio: {}
""")

    monkeypatch.chdir(tmp_path)

    result = cmd_infra_doctor(
        Namespace(
            manifest=str(manifest),
            path=str(infra),
            policy=str(policy),
            skip_tofu=True,
        )
    )

    assert result == 0  # nosec B101
    assert "infra doctor passed" in capsys.readouterr().out  # nosec B101


def test_onboard_repo_renders_manifest_skeleton(capsys) -> None:
    result = cmd_onboard_repo(
        Namespace(
            repo="example-aio",
            profile="changelog-version",
            image_name=None,
            upstream_name="Example",
            local_path_base="<local-checkout-path>",
            format="text",
            dry_run=True,
        )
    )

    assert result == 0  # nosec B101
    output = capsys.readouterr().out
    assert "example-aio:" in output  # nosec B101
    assert "path: <local-checkout-path>/example-aio" in output  # nosec B101
    assert "python -m aio_fleet render-workflow example-aio" in output  # nosec B101
