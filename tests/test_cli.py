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
    cmd_alert_doctor,
    cmd_alert_test,
    cmd_check_run,
    cmd_debt_report,
    cmd_export_app_manifest,
    cmd_fleet_dashboard_commands,
    cmd_fleet_dashboard_update,
    cmd_fleet_report_generate,
    cmd_fleet_report_schema,
    cmd_fleet_report_validate,
    cmd_infra_doctor,
    cmd_onboard_repo,
    cmd_poll,
    cmd_promote_rehab,
    cmd_registry_publish,
    cmd_registry_verify,
    cmd_release_plan,
    cmd_security_audit_workflows,
    cmd_trunk_audit,
    cmd_upstream_assess,
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


def test_alert_doctor_warns_without_required_alerts(capsys) -> None:
    result = cmd_alert_doctor(
        Namespace(
            kuma_url="",
            webhook_url="",
            require_alerts=False,
            format="json",
        )
    )

    assert result == 0  # nosec B101
    report = json.loads(capsys.readouterr().out)
    assert report["warnings"]  # nosec B101
    assert report["ok"] is True  # nosec B101


def test_alert_doctor_can_require_alerts(capsys) -> None:
    result = cmd_alert_doctor(
        Namespace(
            kuma_url="",
            webhook_url="",
            require_alerts=True,
            format="json",
        )
    )

    assert result == 1  # nosec B101
    report = json.loads(capsys.readouterr().out)
    assert report["findings"]  # nosec B101


def test_alert_test_forces_webhook(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "emit_alert",
        lambda *_args, **_kwargs: {"kuma": "would-send", "webhook": "would-send"},
    )

    result = cmd_alert_test(
        Namespace(
            event="upstream-update",
            status="warning",
            summary="test",
            repo=None,
            component=None,
            dedupe_key=None,
            details_url=None,
            kuma_url="https://kuma",
            webhook_url="https://hook",
            webhook_format="json",
            dry_run=True,
            format="json",
        )
    )

    assert result == 0  # nosec B101
    report = json.loads(capsys.readouterr().out)
    assert report["webhook"] == "would-send"  # nosec B101


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


def test_fleet_dashboard_update_dry_run_outputs_state(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manifest, _repo_path = _write_minimal_manifest(tmp_path)

    monkeypatch.setattr(
        cli,
        "dashboard_report",
        lambda *_args, **_kwargs: {
            "body": "# Dashboard\n",
            "state": {"rows": [{"repo": "example-aio"}]},
        },
    )
    monkeypatch.setattr(
        cli,
        "upsert_dashboard_issue",
        lambda **_kwargs: SimpleNamespace(
            action="would-create",
            number=None,
            url="",
        ),
    )

    result = cmd_fleet_dashboard_update(
        Namespace(
            manifest=str(manifest),
            issue_repo="JSONbored/aio-fleet",
            issue_number=None,
            registry=False,
            write=False,
            format="json",
        )
    )

    assert result == 0  # nosec B101
    report = json.loads(capsys.readouterr().out)
    assert report["action"] == "would-create"  # nosec B101
    assert report["state"]["rows"][0]["repo"] == "example-aio"  # nosec B101


def test_fleet_report_generate_outputs_stable_state(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manifest, _repo_path = _write_minimal_manifest(tmp_path)

    monkeypatch.setattr(
        cli,
        "dashboard_report",
        lambda *_args, **_kwargs: {
            "body": "# Dashboard\n",
            "state": {
                "schema_version": 3,
                "generated_at": "2026-05-05T00:00:00+00:00",
                "issue_repo": "JSONbored/aio-fleet",
                "warnings": [],
                "summary": {"posture": "green"},
                "rows": [{"repo": "example-aio"}],
                "activity": [],
                "destination_repos": [],
                "rehab_repos": [],
                "registry": [],
                "releases": [],
                "cleanup": [],
                "workflow": {},
            },
        },
    )

    result = cmd_fleet_report_generate(
        Namespace(
            manifest=str(manifest),
            issue_repo="JSONbored/aio-fleet",
            registry=True,
            include_activity=True,
            stale_days=7,
            format="json",
        )
    )

    assert result == 0  # nosec B101
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == 3  # nosec B101
    assert report["summary"]["posture"] == "green"  # nosec B101
    assert report["rows"][0]["repo"] == "example-aio"  # nosec B101


def test_fleet_report_schema_and_validate(tmp_path: Path, capsys) -> None:
    result = cmd_fleet_report_schema(Namespace())

    assert result == 0  # nosec B101
    schema = json.loads(capsys.readouterr().out)
    assert schema["properties"]["schema_version"]["const"] == 3  # nosec B101
    assert "rows" in schema["required"]  # nosec B101

    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "generated_at": "2026-05-05T00:00:00+00:00",
                "issue_repo": "JSONbored/aio-fleet",
                "warnings": [],
                "summary": {},
                "rows": [],
                "activity": [],
                "destination_repos": [],
                "rehab_repos": [],
                "registry": [],
                "releases": [],
                "cleanup": [],
                "workflow": {},
            }
        )
    )

    result = cmd_fleet_report_validate(Namespace(input=str(report), format="json"))

    assert result == 0  # nosec B101
    assert json.loads(capsys.readouterr().out)["ok"] is True  # nosec B101


def test_fleet_report_validate_rejects_schema_drift(tmp_path: Path, capsys) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"schema_version": 2, "rows": []}))

    result = cmd_fleet_report_validate(Namespace(input=str(report), format="json"))

    assert result == 1  # nosec B101
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False  # nosec B101
    assert any(
        "unsupported schema_version" in failure for failure in payload["failures"]
    )  # nosec B101


def test_release_plan_outputs_all_repo_states(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manifest, _repo_path = _write_minimal_manifest(tmp_path)
    monkeypatch.setattr(
        cli,
        "release_plan_for_manifest",
        lambda *_args, **_kwargs: [
            {
                "repo": "example-aio",
                "state": "release-due",
                "next_version": "1.0.0-aio.2",
                "next_action": "python -m aio_fleet release prepare --repo example-aio --dry-run",
            }
        ],
    )

    result = cmd_release_plan(
        Namespace(
            manifest=str(manifest),
            all=True,
            repo=None,
            repo_path=None,
            registry=False,
            format="json",
        )
    )

    assert result == 0  # nosec B101
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["release_due"] == 1  # nosec B101
    assert payload["repos"][0]["state"] == "release-due"  # nosec B101


def test_security_audit_workflows_reports_findings(tmp_path: Path, capsys) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text("""
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - run: echo hello
""")

    result = cmd_security_audit_workflows(Namespace(path=str(tmp_path), format="json"))

    assert result == 1  # nosec B101
    payload = json.loads(capsys.readouterr().out)
    codes = {finding["code"] for finding in payload["findings"]}
    assert "unpinned-action" in codes  # nosec B101
    assert "checkout-credentials" in codes  # nosec B101


def test_promote_rehab_blocks_legacy_repo(tmp_path: Path, capsys) -> None:
    rehab_path = tmp_path / "nanoclaw-aio"
    rehab_path.mkdir()
    (rehab_path / "cliff.toml").write_text("[changelog]\n")
    active_path = tmp_path / "example-aio"
    active_path.mkdir()
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
dashboard:
  rehab_repos:
    nanoclaw-aio:
      path: {rehab_path}
      github_repo: JSONbored/nanoclaw-aio
repos:
  example-aio:
    path: {active_path}
    app_slug: example-aio
    image_name: jsonbored/example-aio
    docker_cache_scope: example-aio-image
    pytest_image_tag: example-aio:pytest
""")

    result = cmd_promote_rehab(
        Namespace(
            manifest=str(manifest),
            repo="nanoclaw-aio",
            profile="changelog-version",
            dry_run=True,
            format="json",
        )
    )

    assert result == 1  # nosec B101
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is False  # nosec B101
    assert "retired shared path" in payload["findings"][0]  # nosec B101


def test_fleet_dashboard_commands_outputs_github_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "dashboard_issue_commands",
        lambda **_kwargs: {
            "is_dashboard": True,
            "requested": True,
            "commands": {"rescan": True, "upstream_monitor": False},
        },
    )

    result = cmd_fleet_dashboard_commands(
        Namespace(
            issue_repo="JSONbored/aio-fleet",
            issue_number=55,
            format="github-output",
        )
    )

    assert result == 0  # nosec B101
    assert capsys.readouterr().out.splitlines() == [  # nosec B101
        "is_dashboard=true",
        "requested=true",
        "rescan=true",
        "upstream_monitor=false",
    ]


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


def test_latest_main_ci_requires_external_id_bound_check(
    tmp_path: Path, monkeypatch
) -> None:
    manifest, _repo_path = _write_minimal_manifest(tmp_path)
    repo = load_manifest(manifest).repo("example-aio")
    sha = "a" * 40
    expected_external_id = cli.check_external_id(repo, sha=sha, event="push")

    def fake_run(command: list[str], cwd: Path | None = None) -> SimpleNamespace:
        del cwd
        if "commits/main" in command[2]:
            return SimpleNamespace(returncode=0, stdout=f"{sha}\n", stderr="")
        if "check-runs" in command[2]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "check_runs": [
                            {
                                "external_id": "attacker-controlled",
                                "status": "completed",
                                "conclusion": "success",
                            },
                            {
                                "external_id": expected_external_id,
                                "status": "completed",
                                "conclusion": "failure",
                            },
                        ]
                    }
                ),
                stderr="",
            )
        raise AssertionError(command)

    monkeypatch.setattr(cli, "_run", fake_run)

    result = cli._latest_main_ci(repo)

    assert result["state"] == "failure"  # nosec B101


def test_release_version_catches_changelog_system_exit(
    tmp_path: Path, monkeypatch
) -> None:
    _manifest, _repo_path = _write_minimal_manifest(tmp_path)
    repo = load_manifest(_manifest).repo("example-aio")

    monkeypatch.setattr(
        cli,
        "latest_changelog_version",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("bad changelog")),
    )

    assert cli._release_version(repo) == ""  # nosec B101


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
    verify_results = iter([1, 0])
    monkeypatch.delenv("DOCKERHUB_USERNAME", raising=False)
    monkeypatch.delenv("DOCKERHUB_TOKEN", raising=False)
    monkeypatch.delenv("AIO_FLEET_GHCR_TOKEN", raising=False)

    def fake_run(
        command: list[str], cwd: Path | None = None, env=None
    ) -> SimpleNamespace:
        seen["publish_command"] = command
        seen["publish_cwd"] = cwd
        seen["publish_env"] = env
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_registry_verify(args: Namespace) -> int:
        seen.setdefault("verify_calls", []).append(args)
        return next(verify_results)

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
    assert seen["publish_env"] is None  # nosec B101
    verify_calls = seen["verify_calls"]
    assert len(verify_calls) == 2  # nosec B101
    assert verify_calls[0].repo_path == str(repo_path)  # nosec B101
    assert verify_calls[1].sha == "a" * 40  # nosec B101


def test_registry_publish_skips_build_when_tags_are_current(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manifest, repo_path = _write_minimal_manifest(tmp_path)
    monkeypatch.delenv("DOCKERHUB_USERNAME", raising=False)
    monkeypatch.delenv("DOCKERHUB_TOKEN", raising=False)
    monkeypatch.delenv("AIO_FLEET_GHCR_TOKEN", raising=False)

    def fail_run(
        command: list[str], cwd: Path | None = None, env=None
    ) -> SimpleNamespace:
        del command, cwd, env
        raise AssertionError("current registry tags should skip docker build")

    monkeypatch.setattr(cli, "_run", fail_run)
    monkeypatch.setattr(cli, "cmd_registry_verify", lambda _args: 0)

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
    assert (
        "example-aio:aio: registry=already-current" in capsys.readouterr().out
    )  # nosec B101


def test_registry_publish_logs_in_with_temporary_scrubbed_docker_config(
    tmp_path: Path, monkeypatch
) -> None:
    manifest, repo_path = _write_minimal_manifest(tmp_path)
    seen: dict[str, object] = {"login_commands": [], "buildx_commands": []}
    verify_results = iter([1, 0])

    def fake_docker(command: list[str], **kwargs: object):
        docker_env = kwargs["env"]
        assert isinstance(docker_env, dict)  # nosec B101
        assert "DOCKER_CONFIG" in docker_env  # nosec B101
        assert "DOCKERHUB_TOKEN" not in docker_env  # nosec B101
        assert "AIO_FLEET_GHCR_TOKEN" not in docker_env  # nosec B101
        if command[:2] == ["docker", "login"]:
            assert kwargs["input"] in {"hub-token\n", "ghcr-token\n"}  # nosec B101
            seen["login_commands"].append(command)  # type: ignore[union-attr]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:2] == ["docker", "buildx"]:
            seen["buildx_commands"].append(command)  # type: ignore[union-attr]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected docker command: {command}")

    def fake_publish(command: list[str], cwd: Path | None = None, env=None):
        seen["publish_command"] = command
        seen["publish_cwd"] = cwd
        seen["publish_env"] = env
        assert isinstance(env, dict)  # nosec B101
        assert "BUILDX_BUILDER" in env  # nosec B101
        assert "DOCKER_CONFIG" in env  # nosec B101
        assert "DOCKERHUB_TOKEN" not in env  # nosec B101
        assert "AIO_FLEET_GHCR_TOKEN" not in env  # nosec B101
        assert "GH_TOKEN" not in env  # nosec B101
        assert "GITHUB_TOKEN" not in env  # nosec B101
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("DOCKERHUB_USERNAME", "jsonbored")
    monkeypatch.setenv("DOCKERHUB_TOKEN", "hub-token")
    monkeypatch.setenv("AIO_FLEET_GHCR_TOKEN", "ghcr-token")
    monkeypatch.setenv("AIO_FLEET_GHCR_USERNAME", "JSONbored")
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setattr(cli.subprocess, "run", fake_docker)
    monkeypatch.setattr(cli, "_run", fake_publish)
    monkeypatch.setattr(cli, "cmd_registry_verify", lambda _args: next(verify_results))

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
    assert seen["login_commands"] == [  # nosec B101
        [
            "docker",
            "login",
            "docker.io",
            "--username",
            "jsonbored",
            "--password-stdin",
        ],
        [
            "docker",
            "login",
            "ghcr.io",
            "--username",
            "JSONbored",
            "--password-stdin",
        ],
    ]
    buildx_commands = seen["buildx_commands"]
    assert [command[:3] for command in buildx_commands] == [  # nosec B101
        ["docker", "buildx", "create"],
        ["docker", "buildx", "inspect"],
        ["docker", "buildx", "rm"],
    ]
    assert seen["publish_cwd"] == repo_path.resolve()  # nosec B101


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


def test_debt_report_flags_repos_missing_from_github_policy(
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
    assert report["repos"][0]["github_policy_failures"] == [  # nosec B101
        "example-aio: missing from github policy"
    ]
    assert report["repos"][0]["publish"] == "publish=blocked:policy"  # nosec B101


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
            mode="existing",
            shape=None,
        )
    )

    assert result == 0  # nosec B101
    output = capsys.readouterr().out
    assert "example-aio:" in output  # nosec B101
    assert "path: <local-checkout-path>/example-aio" in output  # nosec B101
    assert (  # nosec B101
        "python -m aio_fleet export-app-manifest --repo example-aio --write" in output
    )


def test_onboard_repo_rehab_mode_outputs_checklist(capsys) -> None:
    result = cmd_onboard_repo(
        Namespace(
            repo="nanoclaw-aio",
            profile="changelog-version",
            image_name=None,
            upstream_name="NanoClaw",
            local_path_base="/Users/shadowbook/Documents",
            format="json",
            dry_run=True,
            mode="rehab",
            shape=None,
        )
    )

    assert result == 0  # nosec B101
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "rehab"  # nosec B101
    assert "local repo synced to main" in payload["acceptance_checklist"]  # nosec B101
    assert any(
        "fetch --prune" in item for item in payload["first_commands"]
    )  # nosec B101


def test_onboard_repo_new_from_template_outputs_creation_steps(capsys) -> None:
    result = cmd_onboard_repo(
        Namespace(
            repo="future-aio",
            profile="upstream-aio-track",
            image_name=None,
            upstream_name="Future",
            local_path_base="/Users/shadowbook/Documents",
            format="json",
            dry_run=True,
            mode="new-from-template",
            shape=None,
        )
    )

    assert result == 0  # nosec B101
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "new-from-template"  # nosec B101
    assert any(
        "unraid-aio-template" in step for step in payload["creation_steps"]
    )  # nosec B101
    assert payload["first_commands"][0].startswith("gh repo create")  # nosec B101


def test_onboard_repo_multi_component_shape_outputs_component_pack(capsys) -> None:
    result = cmd_onboard_repo(
        Namespace(
            repo="signoz-aio",
            profile="signoz-suite",
            image_name="jsonbored/signoz-aio",
            upstream_name="SigNoz",
            local_path_base="/Users/shadowbook/Documents",
            format="json",
            dry_run=True,
            mode="existing",
            shape="multi-component",
        )
    )

    assert result == 0  # nosec B101
    payload = json.loads(capsys.readouterr().out)
    assert payload["shape"] == "multi-component"  # nosec B101
    assert payload["manifest_entry"]["components"][1]["name"] == "agent"  # nosec B101
    assert any(
        "multi-component registry verify" in item
        for item in payload["acceptance_checklist"]
    )  # nosec B101


def test_onboard_repo_destination_shape_stays_dashboard_only(capsys) -> None:
    result = cmd_onboard_repo(
        Namespace(
            repo="awesome-unraid",
            profile="template",
            image_name=None,
            upstream_name="Awesome Unraid",
            local_path_base="/Users/shadowbook/Documents",
            format="json",
            dry_run=True,
            mode="existing",
            shape="destination-only",
        )
    )

    assert result == 0  # nosec B101
    payload = json.loads(capsys.readouterr().out)
    assert payload["manifest_entry"] == {}  # nosec B101
    assert "awesome-unraid" in payload["dashboard_entry"]  # nosec B101
    assert all(
        "export-app-manifest" not in command for command in payload["first_commands"]
    )  # nosec B101


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


def test_upstream_write_exports_app_manifest_when_expected(tmp_path: Path) -> None:
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
    upstream_commit_paths:
      - .aio-fleet.yml
""")

    cli._run_generator_for_write(load_manifest(manifest).repo("example-aio"))

    exported = repo_path / ".aio-fleet.yml"
    assert exported.exists()  # nosec B101
    assert "repo: example-aio" in exported.read_text()  # nosec B101


def test_upstream_write_generator_strips_token_environment(
    tmp_path: Path, monkeypatch
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
    generator_check_command: python -V --check
""")
    observed: dict[str, str] = {}

    def fake_run(
        command: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ):
        assert command == ["python", "-V"]  # nosec B101
        assert cwd == repo_path  # nosec B101
        assert isinstance(env, dict)  # nosec B101
        observed.update(env)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setenv("APP_TOKEN", "app")
    monkeypatch.setenv("AIO_FLEET_CHECK_TOKEN", "check")
    monkeypatch.setenv("GH_TOKEN", "gh")
    monkeypatch.setenv("GITHUB_TOKEN", "github")

    cli._run_generator_for_write(load_manifest(manifest).repo("example-aio"))

    assert "APP_TOKEN" not in observed  # nosec B101
    assert "AIO_FLEET_CHECK_TOKEN" not in observed  # nosec B101
    assert "GH_TOKEN" not in observed  # nosec B101
    assert "GITHUB_TOKEN" not in observed  # nosec B101


def test_upstream_assess_outputs_json(tmp_path: Path, monkeypatch, capsys) -> None:
    manifest, _repo_path = _write_minimal_manifest(tmp_path)

    class FakeAssessment:
        safety_level = "warn"

        def to_dict(self):
            return {
                "repo": "example-aio",
                "component": "aio",
                "safety_level": "warn",
                "confidence": 0.6,
                "signals": [],
                "warnings": ["release notes mention review keyword(s): config"],
                "failures": [],
                "next_action": "release notes mention review keyword(s): config",
                "config_delta": "none",
                "template_impact": "no-xml-change",
                "runtime_smoke": "not-configured",
                "changed_files": ["Dockerfile"],
            }

    monkeypatch.setattr(
        cli, "assess_upstream_pr", lambda *_args, **_kwargs: FakeAssessment()
    )

    result = cmd_upstream_assess(
        Namespace(
            manifest=str(manifest),
            repo="example-aio",
            repo_path=None,
            pr=12,
            branch=None,
            format="json",
        )
    )

    assert result == 0  # nosec B101
    payload = json.loads(capsys.readouterr().out)
    assert payload["safety_level"] == "warn"  # nosec B101


def test_upstream_assess_without_pr_uses_monitor_result(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manifest, repo_path = _write_minimal_manifest(tmp_path)

    class FakeAssessment:
        safety_level = "manual"

        def to_dict(self):
            return {
                "repo": "example-aio",
                "component": "aio",
                "safety_level": "manual",
                "confidence": 0.2,
                "signals": ["notify-only upstream strategy"],
                "warnings": [],
                "failures": [],
                "next_action": "manual triage required before source PR",
                "config_delta": "not-assessed",
                "template_impact": "manual",
                "runtime_smoke": "not-configured",
                "changed_files": [],
            }

    monitor_results = [
        SimpleNamespace(
            repo="example-aio",
            component="aio",
            updates_available=True,
            strategy="notify",
        )
    ]
    monkeypatch.setattr(cli, "monitor_repo", lambda *_args, **_kwargs: monitor_results)

    def fake_assess(repo, results, *, changed_files):
        assert repo.path == repo_path  # nosec B101
        assert results == monitor_results  # nosec B101
        assert changed_files == []  # nosec B101
        return FakeAssessment()

    monkeypatch.setattr(cli, "assess_expected_update", fake_assess)

    result = cmd_upstream_assess(
        Namespace(
            manifest=str(manifest),
            repo="example-aio",
            repo_path=None,
            pr=None,
            branch=None,
            format="json",
        )
    )

    assert result == 0  # nosec B101
    payload = json.loads(capsys.readouterr().out)
    assert payload["safety_level"] == "manual"  # nosec B101
