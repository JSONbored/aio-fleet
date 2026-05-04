from __future__ import annotations

from pathlib import Path

from aio_fleet import fleet_dashboard
from aio_fleet.manifest import load_manifest
from aio_fleet.upstream import UpstreamMonitorResult


def test_dashboard_renders_notify_only_update_and_alert_warnings(
    tmp_path: Path, monkeypatch
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
repos:
  mem0-aio:
    path: {repo_path}
    app_slug: mem0-aio
    image_name: jsonbored/mem0-aio
    docker_cache_scope: mem0-aio-image
    pytest_image_tag: mem0-aio:pytest
""")

    monkeypatch.setattr(
        fleet_dashboard,
        "monitor_repo",
        lambda *_args, **_kwargs: [
            UpstreamMonitorResult(
                repo="mem0-aio",
                component="aio",
                name="Mem0",
                strategy="notify",
                source="github-tags",
                current_version="v2.0.0",
                latest_version="v2.0.1",
                current_digest="",
                latest_digest="",
                version_update=True,
                digest_update=False,
                dockerfile=repo_path / "Dockerfile",
                version_key="UPSTREAM_VERSION",
                digest_key="",
                release_notes_url="https://github.com/mem0ai/mem0/releases",
            )
        ],
    )

    report = fleet_dashboard.dashboard_report(
        load_manifest(manifest),
        env={},
    )

    body = str(report["body"])
    assert "manual triage; notify-only strategy" in body  # nosec B101
    assert "AIO_FLEET_KUMA_PUSH_URL is not configured" in body  # nosec B101
    assert report["state"]["rows"][0]["strategy"] == "notify"  # nosec B101


def test_dashboard_marks_unsigned_pr_next_action(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setattr(
        fleet_dashboard,
        "monitor_repo",
        lambda *_args, **_kwargs: [
            UpstreamMonitorResult(
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
                dockerfile=repo_path / "Dockerfile",
                version_key="UPSTREAM_VERSION",
                digest_key="",
                release_notes_url="https://example.invalid/releases",
            )
        ],
    )
    monkeypatch.setattr(
        fleet_dashboard,
        "_open_pr",
        lambda *_args, **_kwargs: {
            "number": 7,
            "url": "https://github.com/JSONbored/example-aio/pull/7",
            "headRefOid": "a" * 40,
            "mergeStateStatus": "BLOCKED",
            "statusCheckRollup": [
                {
                    "name": "aio-fleet / required",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                }
            ],
        },
    )
    monkeypatch.setattr(fleet_dashboard, "_signed_state", lambda *_args: "unsigned")

    report = fleet_dashboard.dashboard_report(
        load_manifest(manifest),
        env={
            "AIO_FLEET_KUMA_PUSH_URL": "https://kuma",
            "AIO_FLEET_ALERT_WEBHOOK_URL": "https://hook",
        },
    )

    row = report["state"]["rows"][0]
    assert row["check"] == "success"  # nosec B101
    assert row["signed"] == "unsigned"  # nosec B101
    assert row["next_action"].startswith("regenerate/update PR")  # nosec B101


def test_issue_number_from_created_issue_url() -> None:
    assert (  # nosec B101
        fleet_dashboard._issue_number_from_url(
            "https://github.com/JSONbored/aio-fleet/issues/55"
        )
        == 55
    )
