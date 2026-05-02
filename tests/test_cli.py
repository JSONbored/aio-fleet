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
    cmd_registry_publish,
    cmd_registry_verify,
    cmd_trunk_audit,
    cmd_upstream_monitor,
    cmd_validate_template_common,
)
from aio_fleet.manifest import load_manifest
from aio_fleet.poll import PollTarget


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


def test_poll_does_not_publish_template_profile_targets(
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
    pytest_image_tag: unraid-aio-template:pytest
    publish_profile: template
""")
    repo = load_manifest(manifest).repo("unraid-aio-template")
    monkeypatch.setattr(
        cli,
        "poll_targets",
        lambda *args, **kwargs: [
            PollTarget(
                repo=repo,
                sha="f" * 40,
                event="push",
                source="main",
            )
        ],
    )

    result = cmd_poll(
        Namespace(
            manifest=str(manifest),
            no_prs=False,
            no_main=False,
            create_checks=False,
            missing_checks_only=False,
            dry_run=False,
            format="json",
        )
    )

    assert result == 0  # nosec B101
    payload = json.loads(capsys.readouterr().out)
    assert payload["targets"][0]["publish"] is False  # nosec B101


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
            trunk=False,
            format="text",
        )
    )

    assert result == 0  # nosec B101
    output = capsys.readouterr().out
    assert "publish=source-ready" in output  # nosec B101
    assert "publish=publish=" not in output  # nosec B101


def test_registry_publish_verifies_with_repo_path(tmp_path: Path, monkeypatch) -> None:
    manifest, repo_path = _write_minimal_manifest(tmp_path)
    seen: dict[str, object] = {}

    def fake_run(command: list[str], cwd: Path | None = None) -> SimpleNamespace:
        seen["publish_command"] = command
        seen["publish_cwd"] = cwd
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_registry_verify(args: Namespace) -> int:
        seen["verify_repo_path"] = args.repo_path
        seen["verify_sha"] = args.sha
        return 0

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(cli, "cmd_registry_verify", fake_registry_verify)

    result = cmd_registry_publish(
        Namespace(
            manifest=str(manifest),
            repo="example-aio",
            repo_path=str(repo_path),
            sha="a" * 40,
            component="aio",
            dry_run=False,
        )
    )

    assert result == 0  # nosec B101
    assert seen["publish_cwd"] == repo_path.resolve()  # nosec B101
    assert seen["verify_repo_path"] == str(repo_path)  # nosec B101
    assert seen["verify_sha"] == "a" * 40  # nosec B101


def test_registry_verify_all_skips_manual_template_publish(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    template_path = tmp_path / "template"
    app_path = tmp_path / "app"
    template_path.mkdir()
    app_path.mkdir()
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
repos:
  unraid-aio-template:
    path: {template_path}
    app_slug: unraid-aio-template
    image_name: jsonbored/unraid-aio-template
    docker_cache_scope: unraid-aio-template-image
    pytest_image_tag: aio-template:pytest
    publish_profile: template
  example-aio:
    path: {app_path}
    app_slug: example-aio
    image_name: jsonbored/example-aio
    docker_cache_scope: example-aio-image
    pytest_image_tag: example-aio:pytest
""")
    verified: list[str] = []

    monkeypatch.setattr(cli, "_git_head", lambda _path: "a" * 40)

    def fake_verify(tags: list[str]) -> list[str]:
        verified.extend(tags)
        return []

    monkeypatch.setattr(cli, "verify_registry_tags", fake_verify)

    result = cmd_registry_verify(
        Namespace(
            manifest=str(manifest),
            all=True,
            repo=None,
            repo_path=None,
            sha=None,
            component="aio",
            include_manual=False,
            dry_run=False,
            format="json",
            verbose=False,
        )
    )

    assert result == 0  # nosec B101
    report = json.loads(capsys.readouterr().out)
    assert report["repos"][0]["repo"] == "unraid-aio-template"  # nosec B101
    assert report["repos"][0]["skipped"] == "manual-template-publish"  # nosec B101
    assert all("unraid-aio-template" not in tag for tag in verified)  # nosec B101
    assert any("example-aio" in tag for tag in verified)  # nosec B101


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
    assert (  # nosec B101
        "python -m aio_fleet export-app-manifest --repo example-aio --write" in output
    )


def test_upstream_monitor_dry_run_reports_updates(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manifest, repo_path = _write_minimal_manifest(tmp_path)
    (repo_path / "Dockerfile").write_text("ARG UPSTREAM_VERSION=1.0.0\n")

    monkeypatch.setattr(
        cli,
        "monitor_repo",
        lambda *_args, **_kwargs: [
            SimpleNamespace(
                repo="example-aio",
                component="aio",
                name="Example",
                strategy="pr",
                source="github-tags",
                current_version="1.0.0",
                latest_version="1.1.0",
                current_digest="",
                latest_digest="",
                version_update=True,
                digest_update=False,
                updates_available=True,
                dockerfile=repo_path / "Dockerfile",
                release_notes_url="https://github.com/example/app/releases",
            )
        ],
    )

    result = cmd_upstream_monitor(
        Namespace(
            manifest=str(manifest),
            all=True,
            repo=None,
            repo_path=None,
            include_manual=False,
            write=False,
            create_pr=False,
            post_check=False,
            dry_run=True,
            format="text",
        )
    )

    assert result == 0  # nosec B101
    assert "example-aio: upstream=updates" in capsys.readouterr().out  # nosec B101
