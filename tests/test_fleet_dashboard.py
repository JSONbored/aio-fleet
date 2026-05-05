from __future__ import annotations

import base64
import json
import subprocess  # nosec B404
from pathlib import Path

import pytest

from aio_fleet import fleet_dashboard
from aio_fleet.manifest import load_manifest
from aio_fleet.upstream import UpstreamMonitorResult


class _FakeAssessment:
    def __init__(self, **values):
        self.values = values

    def to_dict(self):
        return {
            "safety_level": self.values.get("safety_level", "ok"),
            "confidence": self.values.get("confidence", 0.82),
            "config_delta": self.values.get("config_delta", "none"),
            "template_impact": self.values.get("template_impact", "no-xml-change"),
            "runtime_smoke": self.values.get("runtime_smoke", "not-configured"),
            "signals": self.values.get("signals", []),
            "warnings": self.values.get("warnings", []),
            "failures": self.values.get("failures", []),
            "next_action": self.values.get("next_action", "human review and merge"),
        }


@pytest.fixture(autouse=True)
def _stable_dashboard_dependencies(monkeypatch):
    monkeypatch.setattr(
        fleet_dashboard,
        "control_plane_health",
        lambda **_kwargs: {
            "state": "success",
            "workflow": "AIO Fleet Control Plane",
            "repo": "JSONbored/aio-fleet",
            "controls_enabled": True,
            "latest": {"status": "completed", "conclusion": "success"},
            "last_success": {"status": "completed", "conclusion": "success"},
            "last_failure": {},
            "runs": [],
        },
    )

    def fake_release_plan(manifest, **_kwargs):
        return [
            {
                "repo": repo.name,
                "state": "current",
                "latest_release_tag": "",
                "latest_github_release": {"state": "unknown"},
                "next_version": "",
                "next_action": "none",
                "release_due": False,
                "registry_failures": [],
            }
            for repo in manifest.repos.values()
        ]

    monkeypatch.setattr(fleet_dashboard, "release_plan_for_manifest", fake_release_plan)


def test_dashboard_renders_notify_only_update_and_webhook_warning(
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
    monkeypatch.setattr(
        fleet_dashboard,
        "assess_upstream_pr",
        lambda *_args, **_kwargs: _FakeAssessment(
            safety_level="manual",
            next_action="manual triage required before source PR",
        ),
    )

    report = fleet_dashboard.dashboard_report(
        load_manifest(manifest),
        include_activity=False,
        env={},
    )

    body = str(report["body"])
    assert "manual triage; notify-only strategy" in body  # nosec B101
    assert "Safety Review" in body  # nosec B101
    assert (
        "python -m aio_fleet upstream assess --repo mem0-aio --format json" in body
    )  # nosec B101
    assert "AIO_FLEET_KUMA_PUSH_URL is not configured" not in body  # nosec B101
    assert "AIO_FLEET_ALERT_WEBHOOK_URL is not configured" in body  # nosec B101
    assert report["state"]["rows"][0]["strategy"] == "notify"  # nosec B101
    assert report["state"]["summary"]["triage_updates"] == 1  # nosec B101


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
    monkeypatch.setattr(
        fleet_dashboard,
        "assess_upstream_pr",
        lambda *_args, **_kwargs: _FakeAssessment(),
    )

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
      public: true
      role: catalog destination
      catalog_path: {catalog_path}
  rehab_repos:
    nanoclaw-aio:
      path: {rehab_path}
      github_repo: JSONbored/nanoclaw-aio
      public: true
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
            "oldest_issue_age_days": 0,
            "newest_issue_age_days": 0,
            "oldest_pr": {},
            "oldest_issue": {},
            "prs": [],
            "issues": [],
            "needs_response_issues": 0,
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


def test_dashboard_skips_private_active_repo_activity(
    tmp_path: Path, monkeypatch
) -> None:
    repo_path = tmp_path / "private-service-aio"
    repo_path.mkdir()
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
repos:
  private-service-aio:
    path: {repo_path}
    github_repo: PrivateOrg/private-service-aio
    public: false
    app_slug: private-service-aio
    image_name: jsonbored/private-service-aio
    docker_cache_scope: private-service-aio-image
    pytest_image_tag: private-service-aio:pytest
""")

    monkeypatch.setattr(fleet_dashboard, "monitor_repo", lambda *_args, **_kwargs: [])

    def unexpected_activity(*_args: object, **_kwargs: object):
        raise AssertionError("private repo activity should not be queried")

    monkeypatch.setattr(fleet_dashboard, "repo_activity", unexpected_activity)

    report = fleet_dashboard.dashboard_report(load_manifest(manifest), env={})

    activity = report["state"]["activity"][0]
    hidden = _hidden_dashboard_state(str(report["body"]))
    assert activity["activity_state"] == "private-skipped"  # nosec B101
    assert activity["github_repo"] == ""  # nosec B101
    assert activity["prs"] == []  # nosec B101
    assert "PrivateOrg/private-service-aio" not in hidden  # nosec B101
    assert "rotate production signing key" not in hidden  # nosec B101


def test_dashboard_collects_public_active_repo_activity(
    tmp_path: Path, monkeypatch
) -> None:
    repo_path = tmp_path / "example-aio"
    repo_path.mkdir()
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
repos:
  example-aio:
    path: {repo_path}
    github_repo: JSONbored/example-aio
    public: true
    app_slug: example-aio
    image_name: jsonbored/example-aio
    docker_cache_scope: example-aio-image
    pytest_image_tag: example-aio:pytest
""")
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(fleet_dashboard, "monitor_repo", lambda *_args, **_kwargs: [])

    def fake_activity(name: str, github_repo: str, _stale_days: int):
        calls.append((name, github_repo))
        return {
            "repo": name,
            "github_repo": github_repo,
            "activity_state": "ok",
            "open_prs": 1,
            "open_issues": 0,
            "draft_prs": 0,
            "blocked_prs": 0,
            "clean_prs": 1,
            "stale_prs": 0,
            "oldest_pr_age_days": 1,
            "newest_issue_age_days": 0,
            "prs": [{"title": "public maintenance PR", "url": "https://example"}],
        }

    monkeypatch.setattr(fleet_dashboard, "repo_activity", fake_activity)

    report = fleet_dashboard.dashboard_report(load_manifest(manifest), env={})

    assert calls == [("example-aio", "JSONbored/example-aio")]  # nosec B101
    assert "public maintenance PR" in _hidden_dashboard_state(
        str(report["body"])
    )  # nosec B101


def test_dashboard_skips_private_destination_and_rehab_activity(
    tmp_path: Path, monkeypatch
) -> None:
    catalog_path = tmp_path / "private-catalog"
    rehab_path = tmp_path / "private-rehab"
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
dashboard:
  destination_repos:
    private-catalog:
      path: {catalog_path}
      github_repo: PrivateOrg/private-catalog
      catalog_path: {catalog_path}
  rehab_repos:
    private-rehab:
      path: {rehab_path}
      github_repo: PrivateOrg/private-rehab
      status: rehab
repos:
  private-service-aio:
    path: {tmp_path / "private-service-aio"}
    github_repo: PrivateOrg/private-service-aio
    public: false
    app_slug: private-service-aio
    image_name: jsonbored/private-service-aio
    docker_cache_scope: private-service-aio-image
    pytest_image_tag: private-service-aio:pytest
""")

    def unexpected_activity(*_args: object, **_kwargs: object):
        raise AssertionError("private dashboard repo activity should not be queried")

    monkeypatch.setattr(fleet_dashboard, "repo_activity", unexpected_activity)
    monkeypatch.setattr(fleet_dashboard, "catalog_repo_failures", lambda *_args: [])
    monkeypatch.setattr(fleet_dashboard, "monitor_repo", lambda *_args, **_kwargs: [])

    report = fleet_dashboard.dashboard_report(load_manifest(manifest), env={})

    destination = report["state"]["destination_repos"][0]
    rehab = report["state"]["rehab_repos"][0]
    hidden = _hidden_dashboard_state(str(report["body"]))
    assert destination["activity_state"] == "private-skipped"  # nosec B101
    assert rehab["activity_state"] == "private-skipped"  # nosec B101
    assert destination["github_repo"] == ""  # nosec B101
    assert rehab["github_repo"] == ""  # nosec B101
    assert "PrivateOrg/private-catalog" not in hidden  # nosec B101
    assert "PrivateOrg/private-rehab" not in hidden  # nosec B101


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
      public: true
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
    monkeypatch.setattr(
        fleet_dashboard,
        "assess_upstream_pr",
        lambda *_args, **_kwargs: _FakeAssessment(),
    )
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
            "oldest_issue_age_days": 0,
            "newest_issue_age_days": 0,
            "oldest_pr": {},
            "oldest_issue": {},
            "prs": [],
            "issues": [],
            "needs_response_issues": 0,
        },
    )

    report = fleet_dashboard.dashboard_report(load_manifest(manifest), env={})

    destination = report["state"]["destination_repos"][0]
    assert destination["sync_queue_count"] == 1  # nosec B101
    assert destination["sync_queue"][0]["repo"] == "example-aio"  # nosec B101
    assert "| awesome-unraid | catalog destination | ok | 1 |" in str(
        report["body"]
    )  # nosec B101


def test_dashboard_registry_flag_renders_verified_tags(
    tmp_path: Path, monkeypatch
) -> None:
    app_path = tmp_path / "example-aio"
    app_path.mkdir()
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
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
                latest_version="1.0.0",
                current_digest="",
                latest_digest="",
                version_update=False,
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
        "_repo_registry_states",
        lambda _repo: {
            "aio": {
                "repo": "example-aio",
                "component": "aio",
                "sha": "a" * 40,
                "dockerhub": ["jsonbored/example-aio:latest"],
                "ghcr": ["ghcr.io/jsonbored/example-aio:latest"],
                "failures": [],
                "state": "ok",
                "verified_at": "2026-05-05T00:00:00+00:00",
            }
        },
    )

    report = fleet_dashboard.dashboard_report(
        load_manifest(manifest),
        include_activity=False,
        include_registry=True,
        env={"AIO_FLEET_ALERT_WEBHOOK_URL": "https://hook"},
    )

    row = report["state"]["rows"][0]
    assert row["registry"] == "ok:1+1 tags"  # nosec B101
    assert report["state"]["summary"]["registry_verified"] == 1  # nosec B101
    assert "Registry Verification" in str(report["body"])  # nosec B101


def test_dashboard_routes_safety_warning_to_triage(tmp_path: Path, monkeypatch) -> None:
    app_path = tmp_path / "example-aio"
    app_path.mkdir()
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
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
    monkeypatch.setattr(
        fleet_dashboard,
        "assess_upstream_pr",
        lambda *_args, **_kwargs: _FakeAssessment(
            safety_level="warn",
            config_delta="example-aio.xml: +1 -0",
            template_impact="review-template-config-delta",
            runtime_smoke="not-configured",
            warnings=["release notes mention review keyword(s): config"],
            next_action="release notes mention review keyword(s): config",
        ),
    )

    report = fleet_dashboard.dashboard_report(
        load_manifest(manifest),
        include_activity=False,
        env={
            "AIO_FLEET_KUMA_PUSH_URL": "https://kuma",
            "AIO_FLEET_ALERT_WEBHOOK_URL": "https://hook",
        },
    )

    body = str(report["body"])
    row = report["state"]["rows"][0]
    assert row["safety"] == "warn"  # nosec B101
    assert row["config_delta"] == "example-aio.xml: +1 -0"  # nosec B101
    state = report["state"]
    assert state["summary"]["triage_updates"] == 1  # nosec B101
    assert "Needs Triage" in body  # nosec B101
    assert "Safety Review" in body  # nosec B101
    assert "| example-aio | aio | 1.0.0 | 1.1.0 |" in body  # nosec B101
    hidden = _hidden_dashboard_state(body)
    assert '"safety": "warn"' in hidden  # nosec B101


def test_dashboard_state_comment_is_safe_for_pr_titles() -> None:
    state = {
        "generated_at": "2026-05-05T00:00:00+00:00",
        "summary": {},
        "warnings": [],
        "rows": [],
        "activity": [
            {
                "repo": "example-aio",
                "prs": [
                    {
                        "title": "--><a href='https://evil.example'>click</a><!--",
                    }
                ],
            }
        ],
        "destination_repos": [],
        "rehab_repos": [],
    }

    body = fleet_dashboard.render_dashboard(state)
    hidden_block = body.split(fleet_dashboard.STATE_START_BASE64, 1)[1].split(
        fleet_dashboard.STATE_END, 1
    )[0]

    assert "-->" not in hidden_block  # nosec B101
    assert "<a href='https://evil.example'>" not in body  # nosec B101
    assert "evil.example" in _hidden_dashboard_state(body)  # nosec B101


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
                {
                    "number": 9,
                    "title": "one",
                    "url": "https://github.com/JSONbored/example/issues/9",
                    "createdAt": "2026-05-03T00:00:00Z",
                    "labels": [{"name": "needs-response"}],
                },
                {
                    "number": 10,
                    "title": "two",
                    "url": "https://github.com/JSONbored/example/issues/10",
                    "createdAt": "2026-04-20T00:00:00Z",
                    "labels": [],
                },
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
    assert activity["needs_response_issues"] == 1  # nosec B101
    assert activity["oldest_issue"]["number"] == 10  # nosec B101
    assert activity["issues"][0]["number"] == 9  # nosec B101


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

    def fake_run(command: list[str], *, check=True, cwd=None, cli_scope="activity"):
        del check, cwd, cli_scope
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
    def fake_run(command: list[str], *, check=True, cwd=None, cli_scope="activity"):
        del check, cwd, cli_scope
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


def test_dashboard_issue_commands_accepts_labeled_dashboard_issue(
    monkeypatch,
) -> None:
    def fake_run(command: list[str], *, check=True, cwd=None, cli_scope="activity"):
        del check, cwd, cli_scope
        assert command[:4] == ["gh", "issue", "view", "55"]  # nosec B101
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "number": 55,
                    "title": "Fleet Update Dashboard",
                    "state": "OPEN",
                    "body": (
                        "- [x] Run upstream monitor\n"
                        "<!-- aio-fleet-dashboard-state\n{}"
                    ),
                    "labels": [{"name": "fleet-dashboard"}],
                    "url": "https://github.com/JSONbored/aio-fleet/issues/55",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(fleet_dashboard, "_run", fake_run)

    result = fleet_dashboard.dashboard_issue_commands(
        issue_repo="JSONbored/aio-fleet", issue_number=55
    )

    assert result["is_dashboard"] is True  # nosec B101
    assert result["requested"] is True  # nosec B101
    assert result["commands"]["upstream_monitor"] is True  # nosec B101


def test_dashboard_issue_commands_rejects_unlabeled_body_controls(
    monkeypatch,
) -> None:
    def fake_run(command: list[str], *, check=True, cwd=None, cli_scope="activity"):
        del check, cwd, cli_scope
        assert command[:4] == ["gh", "issue", "view", "55"]  # nosec B101
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "number": 55,
                    "title": "Fleet Update Dashboard",
                    "state": "OPEN",
                    "body": (
                        "- [x] Run upstream monitor\n"
                        "<!-- aio-fleet-dashboard-state\n{}"
                    ),
                    "labels": [],
                    "url": "https://github.com/JSONbored/aio-fleet/issues/55",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(fleet_dashboard, "_run", fake_run)

    result = fleet_dashboard.dashboard_issue_commands(
        issue_repo="JSONbored/aio-fleet", issue_number=55
    )

    assert result["is_dashboard"] is False  # nosec B101
    assert result["requested"] is False  # nosec B101
    assert result["commands"] == {}  # nosec B101


def test_dashboard_gh_reads_prefer_app_token(monkeypatch) -> None:
    captured_env: dict[str, str] = {}

    def fake_run(*args: object, **kwargs: object):
        nonlocal captured_env
        captured_env = dict(kwargs.get("env") or {})
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps([]),
            stderr="",
        )

    monkeypatch.setenv("AIO_FLEET_DASHBOARD_TOKEN", "app-token")
    monkeypatch.setenv("AIO_FLEET_ISSUE_TOKEN", "issue-token")
    monkeypatch.setenv("GH_TOKEN", "lower-priority-token")
    monkeypatch.setenv("GITHUB_TOKEN", "repo-token")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = fleet_dashboard._gh_json(["pr", "list", "--repo", "JSONbored/private"])

    assert result == []  # nosec B101
    assert captured_env["GH_TOKEN"] == "app-token"  # nosec B101
    assert "AIO_FLEET_ISSUE_TOKEN" not in captured_env  # nosec B101
    assert "GITHUB_TOKEN" not in captured_env  # nosec B101


def test_dashboard_issue_reads_prefer_issue_token(monkeypatch) -> None:
    captured_env: dict[str, str] = {}

    def fake_run(*args: object, **kwargs: object):
        nonlocal captured_env
        captured_env = dict(kwargs.get("env") or {})
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"number": 55}),
            stderr="",
        )

    monkeypatch.setenv("AIO_FLEET_DASHBOARD_TOKEN", "app-token")
    monkeypatch.setenv("AIO_FLEET_ISSUE_TOKEN", "issue-token")
    monkeypatch.setenv("GH_TOKEN", "lower-priority-token")
    monkeypatch.setenv("GITHUB_TOKEN", "repo-token")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = fleet_dashboard._gh_json(["issue", "view", "55"], cli_scope="issue")

    assert result == {"number": 55}  # nosec B101
    assert captured_env["GH_TOKEN"] == "issue-token"  # nosec B101
    assert "AIO_FLEET_DASHBOARD_TOKEN" not in captured_env  # nosec B101
    assert "GITHUB_TOKEN" not in captured_env  # nosec B101


def _hidden_dashboard_state(body: str) -> str:
    hidden = body.split(fleet_dashboard.STATE_START_BASE64, 1)[1].split(
        fleet_dashboard.STATE_END, 1
    )[0]
    return base64.b64decode(hidden.strip()).decode("utf-8")


def test_dashboard_issue_commands_rejects_unlabeled_non_dashboard_issue(
    monkeypatch,
) -> None:
    def fake_run(command: list[str], *, check=True, cwd=None, cli_scope="activity"):
        del check, cwd, cli_scope
        assert command[:4] == ["gh", "issue", "view", "55"]  # nosec B101
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "number": 55,
                    "title": "Fleet Update Dashboard",
                    "state": "OPEN",
                    "body": "ordinary issue body",
                    "labels": [],
                    "url": "https://github.com/JSONbored/aio-fleet/issues/55",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(fleet_dashboard, "_run", fake_run)

    result = fleet_dashboard.dashboard_issue_commands(
        issue_repo="JSONbored/aio-fleet", issue_number=55
    )

    assert result["is_dashboard"] is False  # nosec B101
    assert result["requested"] is False  # nosec B101
    assert result["commands"] == {}  # nosec B101
