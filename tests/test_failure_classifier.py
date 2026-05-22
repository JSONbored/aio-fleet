from __future__ import annotations

import pytest

from aio_fleet.failure_classifier import classify_failure_text


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
