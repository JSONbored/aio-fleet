from __future__ import annotations

import pytest

from aio_fleet.fleet_queue import (
    action_by_id,
    build_action_queue,
    dispatch_plan,
    enrich_command_center_state,
)


def test_queue_generates_registry_publish_action_with_dispatch_context() -> None:
    sha = "a" * 40
    state = {
        "releases": [
            {
                "repo": "sure-aio",
                "component": "aio",
                "state": "publish-missing",
                "sha": sha,
                "operator_commands": {
                    "release_transaction": "python -c 'raise SystemExit(99)'"
                },
            }
        ]
    }

    actions = build_action_queue(state)

    assert len(actions) == 1  # nosec B101
    action = actions[0]
    assert action["kind"] == "registry-publish"  # nosec B101
    assert action["requires_approval"] is True  # nosec B101
    assert action["next_command"] == (  # nosec B101
        "python -m aio_fleet release transaction --repo sure-aio "
        f"--component aio --sha {sha} --dry-run"
    )
    assert action["workflow_dispatch"]["inputs"] == {  # nosec B101
        "mode": "control-check",
        "repo": "sure-aio",
        "sha": sha,
        "event": "push",
        "publish": "true",
        "publish_component": "aio",
        "dry_run": "true",
    }

    plan = dispatch_plan(action, dry_run=True)

    assert plan["would_dispatch"] is True  # nosec B101
    assert "gh workflow run control-plane.yml" in plan["command"]  # nosec B101
    assert "-f repo=sure-aio" in plan["command"]  # nosec B101
    assert "-f dry_run=true" in plan["command"]  # nosec B101
    assert "-f dry_run=false" not in plan["command"]  # nosec B101


def test_queue_ignores_imported_actions_from_input() -> None:
    actions = build_action_queue(
        {
            "actions": [
                {
                    "id": "imported",
                    "kind": "registry-publish",
                    "repo": "sure-aio",
                    "component": "aio",
                    "next_command": "python -c 'raise SystemExit(99)'",
                }
            ]
        }
    )

    assert actions == []  # nosec B101


def test_queue_skips_rows_with_unsafe_command_fields() -> None:
    actions = build_action_queue(
        {
            "releases": [
                {
                    "repo": "sure-aio;echo-bad",
                    "component": "aio",
                    "state": "publish-missing",
                    "sha": "a" * 40,
                }
            ],
            "failures": [
                {
                    "repo": "aio-fleet",
                    "component": "workflow",
                    "run_id": "123;echo-bad",
                    "sha": "",
                }
            ],
        }
    )

    assert actions == []  # nosec B101


def test_queue_dispatch_refuses_non_dry_run() -> None:
    action = build_action_queue(
        {
            "failures": [
                {
                    "repo": "aio-fleet",
                    "component": "workflow",
                    "run_id": "123",
                    "sha": "",
                }
            ]
        }
    )[0]

    with pytest.raises(RuntimeError, match="dry-run only"):
        dispatch_plan(action, dry_run=False)


def test_queue_dispatch_refuses_unsafe_imported_workflow_dispatch() -> None:
    plan = dispatch_plan(
        {
            "id": "unsafe",
            "requires_approval": True,
            "workflow_dispatch": {
                "workflow": "control-plane.yml",
                "inputs": {"repo": "sure-aio;rm -rf", "mode": "control-check"},
            },
        },
        dry_run=True,
    )

    assert plan["would_dispatch"] is False  # nosec B101
    assert plan["command"] == ""  # nosec B101


def test_enriched_state_adds_command_center_sections() -> None:
    state = {
        "summary": {"posture": "blocked"},
        "releases": [
            {
                "repo": "sure-aio",
                "component": "aio",
                "state": "catalog-sync-needed",
                "sha": "b" * 40,
                "catalog_sync_needed": True,
                "next_action": "python -m aio_fleet sync-catalog --dry-run",
            }
        ],
        "cleanup": [
            {
                "repo": "sure-aio",
                "state": "drift",
                "findings_count": 1,
                "findings": [{"path": "release-agent.yml", "reason": "legacy"}],
            }
        ],
        "failures": [{"repo": "aio-fleet", "run_id": "123", "sha": ""}],
    }

    enriched = enrich_command_center_state(state)

    assert enriched["actions"]  # nosec B101
    assert enriched["catalog"]["state"] == "drift"  # nosec B101
    assert enriched["standards"]["state"] == "drift"  # nosec B101
    assert enriched["candidates"]["state"] == "planning"  # nosec B101
    assert enriched["summary"]["actions_queued"] == len(  # nosec B101
        enriched["actions"]
    )
    drift = [
        action for action in enriched["actions"] if action["kind"] == "drift-repair"
    ][0]
    assert "--dry-run" in drift["next_command"]  # nosec B101
    assert action_by_id(enriched["actions"], enriched["actions"][0]["id"])  # nosec B101
