from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

from aio_fleet import cli
from aio_fleet.cli import _repo_python, cmd_debt_report, cmd_trunk_audit


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
