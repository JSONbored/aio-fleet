from __future__ import annotations

import json
import subprocess
from pathlib import Path

from aio_fleet import workflow_jobs
from aio_fleet.workflow_jobs import (
    poll_outputs,
    registry_audit_checkouts,
    render_registry_summary,
    render_upstream_summary,
)


def test_poll_outputs_writes_github_matrix(tmp_path: Path) -> None:
    report = tmp_path / "poll-targets.json"
    output = tmp_path / "github-output.txt"
    report.write_text(json.dumps({"targets": [{"repo": "sure-aio"}]}))

    payload = poll_outputs(
        report_path=report,
        run_checks=True,
        github_output=output,
    )

    assert payload["has_targets"] is True  # nosec B101
    text = output.read_text()
    assert "run_checks=true" in text  # nosec B101
    assert "targets<<__AIO_FLEET_TARGETS__" in text  # nosec B101


def test_upstream_summary_renders_updates(tmp_path: Path) -> None:
    report = tmp_path / "upstream-report.json"
    report.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "repo": "sure-aio",
                        "results": [
                            {
                                "component": "aio",
                                "current_version": "0.7.0",
                                "latest_version": "0.7.1",
                                "updates_available": True,
                            }
                        ],
                    }
                ]
            }
        )
    )

    text = render_upstream_summary(report_path=report, output_path=None)

    assert "`sure-aio`: updates available" in text  # nosec B101
    assert "0.7.0 -> 0.7.1" in text  # nosec B101


def test_upstream_summary_renders_blocked_submodule_ref(tmp_path: Path) -> None:
    report = tmp_path / "upstream-report.json"
    report.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "repo": "mem0-aio",
                        "results": [
                            {
                                "component": "openmemory",
                                "current_version": "v2.0.1",
                                "latest_version": "v2.0.2",
                                "updates_available": True,
                                "state": "blocked",
                                "blocked": True,
                                "blocked_reason": "missing configured submodule ref",
                                "next_action": (
                                    "create and push codex/openmemory-v2.0.2-aio"
                                ),
                            }
                        ],
                    }
                ]
            }
        )
    )

    text = render_upstream_summary(report_path=report, output_path=None)

    assert "`mem0-aio`: blocked" in text  # nosec B101
    assert "missing configured submodule ref" in text  # nosec B101


def test_registry_summary_renders_missing_tags(tmp_path: Path) -> None:
    report = tmp_path / "registry-report.json"
    report.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "repo": "dify-aio",
                        "component": "aio",
                        "sha": "a" * 40,
                        "dockerhub": ["jsonbored/dify-aio:latest"],
                        "ghcr": [],
                        "failures": ["missing tag"],
                    }
                ]
            }
        )
    )

    text = render_registry_summary(report_path=report, status="1", output_path=None)

    assert "Registry Audit" in text  # nosec B101
    assert "dify-aio: missing tag" in text  # nosec B101


def test_registry_audit_skips_components_not_required_for_current_head(
    monkeypatch, tmp_path: Path
) -> None:
    manifest = tmp_path / "fleet.yml"
    checkout_root = tmp_path / "checkouts"
    output = tmp_path / "registry-report.json"
    manifest.write_text("""
owner: JSONbored
repos:
  sure-aio:
    path: /tmp/sure-aio
    app_slug: sure-aio
    image_name: jsonbored/sure-aio
    docker_cache_scope: sure-aio
    pytest_image_tag: sure-aio:pytest
    publish_profile: multi-component
    components:
      aio: {}
      sure-alpha:
        image_name: jsonbored/sure-aio
""")

    def fake_checkout_refs(refs, *, token: str, submodules: str):
        results = []
        for name, github_repo, path in refs:
            path.mkdir(parents=True)
            results.append(
                {"repo": name, "github_repo": github_repo, "path": str(path)}
            )
        return results

    def fake_check_output(*_args, **_kwargs):
        return "a" * 40

    verify_components: list[str] = []

    def fake_run(args, **_kwargs):
        component = args[args.index("--component") + 1]
        verify_components.append(component)
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(
                {
                    "repos": [
                        {
                            "repo": "sure-aio",
                            "component": component,
                            "sha": "a" * 40,
                            "dockerhub": ["jsonbored/sure-aio:latest-alpha"],
                            "ghcr": ["ghcr.io/jsonbored/sure-aio:latest-alpha"],
                            "failures": [],
                        }
                    ]
                }
            ),
            "",
        )

    monkeypatch.setattr(workflow_jobs, "_checkout_refs", fake_checkout_refs)
    monkeypatch.setattr(workflow_jobs.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(workflow_jobs.subprocess, "run", fake_run)
    monkeypatch.setattr(
        workflow_jobs,
        "publish_components_required",
        lambda *_args, **_kwargs: ["sure-alpha"],
    )

    report = registry_audit_checkouts(
        manifest_path=manifest,
        checkout_root=checkout_root,
        output_path=output,
        token="token",  # nosec B106
        github_output=None,
    )

    assert verify_components == ["sure-alpha"]  # nosec B101
    assert report["status"] == 0  # nosec B101
    rows = {(row["component"], row.get("skipped")) for row in report["repos"]}
    assert ("aio", "not-publish-related") in rows  # nosec B101
    assert ("sure-alpha", None) in rows  # nosec B101
