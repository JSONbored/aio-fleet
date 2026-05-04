from __future__ import annotations

import json
import subprocess  # nosec B404
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
        include_activity=False,
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
        include_activity=False,
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


def test_dashboard_renders_destination_and_rehab_groups(
    tmp_path: Path, monkeypatch
) -> None:
    active_path = tmp_path / "active"
    active_path.mkdir()
    catalog_path = tmp_path / "awesome-unraid"
    catalog_path.mkdir()
    rehab_path = tmp_path / "nanoclaw-aio"
    rehab_path.mkdir()
    (rehab_path / "cliff.toml").write_text("[changelog]\n")
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
dashboard:
  destination_repos:
    awesome-unraid:
      path: {catalog_path}
      github_repo: JSONbored/awesome-unraid
      role: catalog destination
      catalog_path: {catalog_path}
  rehab_repos:
    nanoclaw-aio:
      path: {rehab_path}
      github_repo: JSONbored/nanoclaw-aio
      status: rehab
repos:
  example-aio:
    path: {active_path}
    app_slug: example-aio
    image_name: jsonbored/example-aio
    docker_cache_scope: example-aio-image
    pytest_image_tag: example-aio:pytest
""")

    monkeypatch.setattr(
        fleet_dashboard,
        "monitor_repo",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        fleet_dashboard,
        "repo_activity",
        lambda name, github_repo, _stale_days: {
            "repo": name,
            "github_repo": github_repo,
            "activity_state": "ok",
            "open_prs": 1 if name == "awesome-unraid" else 0,
            "open_issues": 2 if name == "nanoclaw-aio" else 0,
            "draft_prs": 0,
            "blocked_prs": 0,
            "clean_prs": 1 if name == "awesome-unraid" else 0,
            "stale_prs": 0,
            "oldest_pr_age_days": 0,
            "newest_issue_age_days": 0,
            "prs": [],
        },
    )
    monkeypatch.setattr(fleet_dashboard, "catalog_repo_failures", lambda *_args: [])
    monkeypatch.setattr(
        fleet_dashboard,
        "_git_state",
        lambda _path: {"path_exists": True, "branch": "main", "dirty": False},
    )

    report = fleet_dashboard.dashboard_report(load_manifest(manifest), env={})

    state = report["state"]
    body = str(report["body"])
    assert state["summary"]["destination_repos"] == 1  # nosec B101
    assert state["summary"]["rehab_repos"] == 1  # nosec B101
    assert set(load_manifest(manifest).repos) == {"example-aio"}  # nosec B101
    assert state["destination_repos"][0]["repo"] == "awesome-unraid"  # nosec B101
    assert state["rehab_repos"][0]["repo"] == "nanoclaw-aio"  # nosec B101
    assert state["rehab_repos"][0]["cleanup_findings"] == 1  # nosec B101
    assert "Destination Repo" in body  # nosec B101
    assert "Rehab / Onboarding" in body  # nosec B101
    assert "- [ ] Rescan dashboard" in body  # nosec B101
    assert "- [ ] Run upstream monitor" in body  # nosec B101
    assert not any(row["repo"] == "nanoclaw-aio" for row in state["rows"])  # nosec B101
    assert not any(
        row["repo"] == "awesome-unraid" for row in state["rows"]
    )  # nosec B101


def test_destination_row_tracks_ready_source_sync_queue(
    tmp_path: Path, monkeypatch
) -> None:
    app_path = tmp_path / "example-aio"
    app_path.mkdir()
    catalog_path = tmp_path / "awesome-unraid"
    catalog_path.mkdir()
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
dashboard:
  destination_repos:
    awesome-unraid:
      path: {catalog_path}
      github_repo: JSONbored/awesome-unraid
      role: catalog destination
      catalog_path: {catalog_path}
repos:
  example-aio:
    path: {app_path}
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
                dockerfile=app_path / "Dockerfile",
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
            "number": 12,
            "url": "https://github.com/JSONbored/example-aio/pull/12",
            "headRefOid": "b" * 40,
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [
                {
                    "name": "aio-fleet / required",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                }
            ],
        },
    )
    monkeypatch.setattr(fleet_dashboard, "_signed_state", lambda *_args: "verified")
    monkeypatch.setattr(fleet_dashboard, "catalog_repo_failures", lambda *_args: [])
    monkeypatch.setattr(
        fleet_dashboard,
        "repo_activity",
        lambda name, github_repo, _stale_days: {
            "repo": name,
            "github_repo": github_repo,
            "activity_state": "ok",
            "open_prs": 0,
            "open_issues": 0,
            "draft_prs": 0,
            "blocked_prs": 0,
            "clean_prs": 0,
            "stale_prs": 0,
            "oldest_pr_age_days": 0,
            "newest_issue_age_days": 0,
            "prs": [],
        },
    )

    report = fleet_dashboard.dashboard_report(load_manifest(manifest), env={})

    destination = report["state"]["destination_repos"][0]
    assert destination["sync_queue_count"] == 1  # nosec B101
    assert destination["sync_queue"][0]["repo"] == "example-aio"  # nosec B101
    assert "| awesome-unraid | catalog destination | ok | 1 |" in str(
        report["body"]
    )  # nosec B101


def test_repo_activity_classifies_open_prs_and_issues(monkeypatch) -> None:
    def fake_gh_json(args: list[str]):
        if args[:2] == ["pr", "list"]:
            return [
                {
                    "number": 1,
                    "title": "ready",
                    "url": "https://github.com/JSONbored/example/pull/1",
                    "isDraft": False,
                    "mergeStateStatus": "CLEAN",
                    "statusCheckRollup": [],
                    "createdAt": "2026-04-24T00:00:00Z",
                },
                {
                    "number": 2,
                    "title": "draft",
                    "url": "https://github.com/JSONbored/example/pull/2",
                    "isDraft": True,
                    "mergeStateStatus": "CLEAN",
                    "statusCheckRollup": [],
                    "createdAt": "2026-05-04T00:00:00Z",
                },
                {
                    "number": 3,
                    "title": "blocked",
                    "url": "https://github.com/JSONbored/example/pull/3",
                    "isDraft": False,
                    "mergeStateStatus": "DIRTY",
                    "statusCheckRollup": [],
                    "createdAt": "2026-05-04T00:00:00Z",
                },
            ]
        if args[:2] == ["issue", "list"]:
            return [
                {"number": 9, "title": "one", "createdAt": "2026-05-03T00:00:00Z"},
                {"number": 10, "title": "two", "createdAt": "2026-05-04T00:00:00Z"},
            ]
        raise AssertionError(args)

    monkeypatch.setattr(fleet_dashboard, "_gh_json", fake_gh_json)

    activity = fleet_dashboard.repo_activity(
        "example-aio", "JSONbored/example-aio", stale_days=7
    )

    assert activity["open_prs"] == 3  # nosec B101
    assert activity["clean_prs"] == 1  # nosec B101
    assert activity["draft_prs"] == 1  # nosec B101
    assert activity["blocked_prs"] == 1  # nosec B101
    assert activity["stale_prs"] == 1  # nosec B101
    assert activity["open_issues"] == 2  # nosec B101


def test_repo_activity_failure_is_non_blocking(monkeypatch) -> None:
    def fake_gh_json(_args: list[str]):
        raise RuntimeError("api down")

    monkeypatch.setattr(fleet_dashboard, "_gh_json", fake_gh_json)

    activity = fleet_dashboard.repo_activity(
        "example-aio", "JSONbored/example-aio", stale_days=7
    )

    assert activity["activity_state"] == "unknown"  # nosec B101
    assert activity["open_prs"] == "unknown"  # nosec B101


def test_dashboard_command_parser_detects_checked_controls() -> None:
    commands = fleet_dashboard.dashboard_commands_from_body(
        "\n".join(
            [
                "## Controls",
                "",
                "- [x] Rescan dashboard",
                "- [ ] Run upstream monitor",
            ]
        )
    )

    assert commands == {  # nosec B101
        "rescan": True,
        "upstream_monitor": False,
    }


def test_find_dashboard_issue_prefers_labeled_canonical_issue(monkeypatch) -> None:
    responses = {
        (
            "issue",
            "list",
            "--repo",
            "JSONbored/aio-fleet",
            "--state",
            "open",
            "--label",
        ): [
            {
                "number": 55,
                "title": "Fleet Update Dashboard",
                "url": "https://github.com/JSONbored/aio-fleet/issues/55",
                "updatedAt": "2026-05-04T19:00:00Z",
                "body": "<!-- aio-fleet-dashboard-state",
                "labels": [{"name": "fleet-dashboard"}],
            }
        ],
        (
            "issue",
            "list",
            "--repo",
            "JSONbored/aio-fleet",
            "--state",
            "open",
            "--search",
        ): [
            {
                "number": 58,
                "title": "Fleet Update Dashboard",
                "url": "https://github.com/JSONbored/aio-fleet/issues/58",
                "updatedAt": "2026-05-04T12:00:00Z",
                "body": "<!-- aio-fleet-dashboard-state",
                "labels": [],
            },
            {
                "number": 55,
                "title": "Fleet Update Dashboard",
                "url": "https://github.com/JSONbored/aio-fleet/issues/55",
                "updatedAt": "2026-05-04T19:00:00Z",
                "body": "<!-- aio-fleet-dashboard-state",
                "labels": [{"name": "fleet-dashboard"}],
            },
        ],
    }

    def fake_run(command: list[str], *, check=True, cwd=None):
        del check, cwd
        key = tuple(command[1:8])
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(responses[key]),
            stderr="",
        )

    monkeypatch.setattr(fleet_dashboard, "_run", fake_run)

    issue = fleet_dashboard._find_dashboard_issue(
        "JSONbored/aio-fleet", label="fleet-dashboard"
    )

    assert issue is not None  # nosec B101
    assert issue["number"] == 55  # nosec B101


def test_dashboard_issue_by_number_uses_direct_view(monkeypatch) -> None:
    def fake_run(command: list[str], *, check=True, cwd=None):
        del check, cwd
        assert command[:4] == ["gh", "issue", "view", "55"]  # nosec B101
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "number": 55,
                    "title": "Fleet Update Dashboard",
                    "url": "https://github.com/JSONbored/aio-fleet/issues/55",
                    "updatedAt": "2026-05-04T19:00:00Z",
                    "body": "<!-- aio-fleet-dashboard-state",
                    "labels": [{"name": "fleet-dashboard"}],
                    "state": "OPEN",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(fleet_dashboard, "_run", fake_run)

    issue = fleet_dashboard._dashboard_issue_by_number("JSONbored/aio-fleet", 55)

    assert issue is not None  # nosec B101
    assert issue["number"] == 55  # nosec B101
