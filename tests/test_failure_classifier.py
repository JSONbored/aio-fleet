from __future__ import annotations

import pytest

from aio_fleet.failure_classifier import classify_failure_text, classify_workflow_state


@pytest.mark.parametrize(
    ("snippet", "root_cause"),
    [
        (
            "permission_denied: write_package for ghcr.io/jsonbored/sure-aio",
            "ghcr-access",
        ),
        ("Docker Hub publish credentials are missing", "dockerhub-auth"),
        (
            "missing or unreachable registry tags: jsonbored/sure-aio:latest",
            "registry-tags-missing",
        ),
        (
            "fetch tags before trusting release due from a shallow checkout",
            "release-history-incomplete",
        ),
        (
            "required-check-missing: aio-fleet / required did not pass",
            "required-check-missing",
        ),
        ("pytest failed in tests/integration/test_runtime.py", "integration-test"),
        ("catalog-sync-needed for awesome-unraid XML", "catalog-drift"),
        ("Resource not accessible by integration", "github-app-permission"),
        (
            "upstream monitor blocked: missing configured submodule ref",
            "upstream-blocked",
        ),
        ("workflow timed out after 60 minutes", "workflow-timeout"),
    ],
)
def test_classify_known_failure_modes(snippet: str, root_cause: str) -> None:
    result = classify_failure_text(snippet, metadata={"run_id": "12345"})

    assert result["root_cause"] == root_cause  # nosec B101
    assert result["run_id"] == "12345"  # nosec B101
    assert result["next_action"]  # nosec B101


def test_classification_redacts_public_text() -> None:
    result = classify_failure_text(
        "pytest failed from /Users/shadowbook/Documents/aio-fleet/.venv/bin/python",
        metadata={"run_id": "12345"},
    )

    assert "/Users/shadowbook" not in str(result)  # nosec B101
    assert result["summary"] == "integration-test failure detected"  # nosec B101


def test_workflow_classification_ignores_recovered_failure() -> None:
    failures = classify_workflow_state(
        {
            "repo": "JSONbored/aio-fleet",
            "state": "success",
            "latest": {
                "id": 200,
                "conclusion": "success",
                "updated_at": "2026-05-22T15:21:44Z",
            },
            "last_failure": {
                "id": 100,
                "conclusion": "failure",
                "updated_at": "2026-05-22T13:15:04Z",
                "title": "AIO Fleet Control Plane",
            },
        }
    )

    assert failures == []  # nosec B101


def test_workflow_classification_keeps_current_failure() -> None:
    failures = classify_workflow_state(
        {
            "repo": "JSONbored/aio-fleet",
            "state": "failure",
            "latest": {
                "id": 200,
                "conclusion": "failure",
                "updated_at": "2026-05-22T15:21:44Z",
            },
            "last_failure": {
                "id": 200,
                "conclusion": "failure",
                "updated_at": "2026-05-22T15:21:44Z",
                "title": "AIO Fleet Control Plane",
            },
        }
    )

    assert len(failures) == 1  # nosec B101
    assert failures[0]["run_id"] == "200"  # nosec B101
