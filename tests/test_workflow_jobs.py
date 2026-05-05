from __future__ import annotations

import json
from pathlib import Path

from aio_fleet.workflow_jobs import (
    poll_outputs,
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
