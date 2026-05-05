from __future__ import annotations

from pathlib import Path

from aio_fleet import safety
from aio_fleet.manifest import load_manifest
from aio_fleet.upstream import UpstreamMonitorResult


def test_pin_only_update_is_ok(tmp_path: Path, monkeypatch) -> None:
    repo = load_manifest(_manifest(tmp_path)).repo("example-aio")
    _write_xml(tmp_path / "example-aio.xml", targets=["8080"])
    monkeypatch.setattr(safety, "release_notes_text", lambda _result: "Bug fixes")

    assessment = safety.assess_upstream_pr(
        repo,
        result=_result(tmp_path),
        pr=_pr(
            files=["Dockerfile"], checks=[_check("aio-fleet / required", "SUCCESS")]
        ),
        signed_state="verified",
        check_state="success",
    )

    assert assessment.safety_level == "ok"  # nosec B101
    assert assessment.config_delta == "none"  # nosec B101
    assert assessment.template_impact == "manifest-targets-present"  # nosec B101


def test_runtime_smoke_deferred_is_an_ok_signal(tmp_path: Path, monkeypatch) -> None:
    repo = load_manifest(
        _manifest(tmp_path, integration_args="tests/integration")
    ).repo("example-aio")
    _write_xml(tmp_path / "example-aio.xml", targets=["8080"])
    monkeypatch.setattr(safety, "release_notes_text", lambda _result: "Bug fixes")

    assessment = safety.assess_upstream_pr(
        repo,
        result=_result(tmp_path),
        pr=_pr(
            files=["Dockerfile"], checks=[_check("aio-fleet / required", "SUCCESS")]
        ),
        signed_state="verified",
        check_state="success",
    )

    assert assessment.safety_level == "ok"  # nosec B101
    assert assessment.runtime_smoke == "deferred-to-main"  # nosec B101
    assert any("deferred" in item for item in assessment.signals)  # nosec B101
    assert not assessment.warnings  # nosec B101


def test_runtime_smoke_failure_blocks(tmp_path: Path, monkeypatch) -> None:
    repo = load_manifest(
        _manifest(tmp_path, integration_args="tests/integration")
    ).repo("example-aio")
    _write_xml(tmp_path / "example-aio.xml", targets=["8080"])
    monkeypatch.setattr(safety, "release_notes_text", lambda _result: "Bug fixes")

    assessment = safety.assess_upstream_pr(
        repo,
        result=_result(tmp_path),
        pr=_pr(
            files=["Dockerfile"],
            checks=[
                _check("aio-fleet / required", "SUCCESS"),
                _check("integration-tests", "FAILURE"),
            ],
        ),
        signed_state="verified",
        check_state="success",
    )

    assert assessment.safety_level == "blocked"  # nosec B101
    assert assessment.runtime_smoke == "failed"  # nosec B101
    assert any(  # nosec B101
        "runtime/integration check failed" in item for item in assessment.failures
    )


def test_xml_target_delta_warns(tmp_path: Path, monkeypatch) -> None:
    repo = load_manifest(
        _manifest(tmp_path, commit_paths=["Dockerfile", "example-aio.xml"])
    ).repo("example-aio")
    monkeypatch.setattr(safety, "release_notes_text", lambda _result: "Bug fixes")
    monkeypatch.setattr(
        safety,
        "_ref_file_text",
        lambda _repo, _path, ref: _xml(["8080"] if ref == "main" else ["8080", "9090"]),
    )

    assessment = safety.assess_upstream_pr(
        repo,
        result=_result(tmp_path),
        pr=_pr(files=["Dockerfile", "example-aio.xml"]),
        signed_state="verified",
        check_state="success",
    )

    assert assessment.safety_level == "warn"  # nosec B101
    assert assessment.config_delta == "example-aio.xml: +1 -0"  # nosec B101
    assert any(
        "Config Target delta" in item for item in assessment.warnings
    )  # nosec B101


def test_missing_manifest_required_target_blocks(tmp_path: Path, monkeypatch) -> None:
    repo = load_manifest(_manifest(tmp_path, required_targets=["REQUIRED_ENV"])).repo(
        "example-aio"
    )
    _write_xml(tmp_path / "example-aio.xml", targets=["8080"])
    monkeypatch.setattr(safety, "release_notes_text", lambda _result: "Bug fixes")

    assessment = safety.assess_upstream_pr(
        repo,
        result=_result(tmp_path),
        pr=_pr(files=["Dockerfile"]),
        signed_state="verified",
        check_state="success",
    )

    assert assessment.safety_level == "blocked"  # nosec B101
    assert any(
        "missing manifest-required" in item for item in assessment.failures
    )  # nosec B101


def test_release_note_keyword_warns(tmp_path: Path, monkeypatch) -> None:
    repo = load_manifest(_manifest(tmp_path)).repo("example-aio")
    _write_xml(tmp_path / "example-aio.xml", targets=["8080"])
    monkeypatch.setattr(
        safety, "release_notes_text", lambda _result: "Breaking config migration"
    )

    assessment = safety.assess_upstream_pr(
        repo,
        result=_result(tmp_path),
        pr=_pr(files=["Dockerfile"]),
        signed_state="verified",
        check_state="success",
    )

    assert assessment.safety_level == "warn"  # nosec B101
    assert any("breaking" in item for item in assessment.warnings)  # nosec B101


def test_unexpected_file_blocks(tmp_path: Path, monkeypatch) -> None:
    repo = load_manifest(_manifest(tmp_path)).repo("example-aio")
    _write_xml(tmp_path / "example-aio.xml", targets=["8080"])
    monkeypatch.setattr(safety, "release_notes_text", lambda _result: "Bug fixes")

    assessment = safety.assess_upstream_pr(
        repo,
        result=_result(tmp_path),
        pr=_pr(files=["Dockerfile", "rootfs/start.sh"]),
        signed_state="verified",
        check_state="success",
    )

    assert assessment.safety_level == "blocked"  # nosec B101
    assert any(
        "unexpected upstream PR file" in item for item in assessment.failures
    )  # nosec B101


def test_notify_only_update_is_manual(tmp_path: Path) -> None:
    repo = load_manifest(_manifest(tmp_path)).repo("example-aio")

    assessment = safety.assess_upstream_pr(
        repo,
        result=_result(tmp_path, strategy="notify"),
        pr=None,
    )

    assert assessment.safety_level == "manual"  # nosec B101
    assert (
        assessment.next_action == "manual triage required before source PR"
    )  # nosec B101


def _manifest(
    tmp_path: Path,
    *,
    commit_paths: list[str] | None = None,
    required_targets: list[str] | None = None,
    integration_args: str = "",
) -> Path:
    validation = ""
    if required_targets is not None:
        validation = "\n    validation:\n      required_targets:\n" + "".join(
            f"        - {target}\n" for target in required_targets
        )
    else:
        validation = "\n    validation:\n      required_targets:\n        - 8080\n"
    paths = commit_paths or ["Dockerfile"]
    path_lines = "".join(f"      - {path}\n" for path in paths)
    integration_line = (
        f"    integration_pytest_args: {integration_args}\n" if integration_args else ""
    )
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
repos:
  example-aio:
    path: {tmp_path}
    app_slug: example-aio
    image_name: jsonbored/example-aio
    docker_cache_scope: example-aio-image
    pytest_image_tag: example-aio:pytest
{integration_line.rstrip()}
    upstream_commit_paths:
{path_lines}
    catalog_assets:
      - source: example-aio.xml
        target: example-aio.xml
{validation}
""")
    (tmp_path / "Dockerfile").write_text("ARG UPSTREAM_VERSION=1.1.0\n")
    return manifest


def _result(tmp_path: Path, *, strategy: str = "pr") -> UpstreamMonitorResult:
    return UpstreamMonitorResult(
        repo="example-aio",
        component="aio",
        name="Example",
        strategy=strategy,
        source="github-tags",
        current_version="1.0.0",
        latest_version="1.1.0",
        current_digest="",
        latest_digest="",
        version_update=True,
        digest_update=False,
        dockerfile=tmp_path / "Dockerfile",
        version_key="UPSTREAM_VERSION",
        digest_key="",
        release_notes_url="https://github.com/example/app/releases",
    )


def _pr(
    *,
    files: list[str],
    checks: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "number": 1,
        "baseRefName": "main",
        "headRefName": "codex/upstream-example-aio-1.1.0",
        "files": [{"path": path} for path in files],
        "statusCheckRollup": checks or [_check("aio-fleet / required", "SUCCESS")],
    }


def _check(name: str, conclusion: str) -> dict[str, str]:
    return {"name": name, "status": "COMPLETED", "conclusion": conclusion}


def _write_xml(path: Path, *, targets: list[str]) -> None:
    path.write_text(_xml(targets))


def _xml(targets: list[str]) -> str:
    configs = "\n".join(
        f'  <Config Name="{target}" Target="{target}" Type="Variable" />'
        for target in targets
    )
    return f'<?xml version="1.0"?><Container>{configs}</Container>'
