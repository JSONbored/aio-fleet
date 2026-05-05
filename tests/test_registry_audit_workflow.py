from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "registry-audit.yml"
SECRET_ENV_KEYS = {
    "AIO_FLEET_APP_ID",
    "AIO_FLEET_APP_INSTALLATION_ID",
    "AIO_FLEET_APP_PRIVATE_KEY",
    "AIO_FLEET_KUMA_PUSH_URL",
    "AIO_FLEET_ALERT_WEBHOOK_URL",
}


def test_registry_audit_scopes_secrets_to_required_steps() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    job = workflow["jobs"]["registry-audit"]

    assert not SECRET_ENV_KEYS.intersection(job.get("env", {}))  # nosec B101

    token_step = _step(job, "Resolve GitHub App token")
    assert {  # nosec B101
        "AIO_FLEET_APP_ID",
        "AIO_FLEET_APP_INSTALLATION_ID",
        "AIO_FLEET_APP_PRIVATE_KEY",
    }.issubset(token_step["env"])

    alert_step = _step(job, "Alert registry audit")
    assert {  # nosec B101
        "AIO_FLEET_KUMA_PUSH_URL",
        "AIO_FLEET_ALERT_WEBHOOK_URL",
    }.issubset(alert_step["env"])


def test_registry_audit_sanitizes_verify_subprocess_environment() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    verify = _step(workflow["jobs"]["registry-audit"], "Verify registry tags")

    assert "verify_env" in verify["run"]  # nosec B101
    assert "env=verify_env" in verify["run"]  # nosec B101
    assert (
        "APP_TOKEN"
        not in verify["run"].split("verify_env = ", 1)[1].split("report = ", 1)[0]
    )  # nosec B101


def _step(job: dict[str, object], name: str) -> dict[str, object]:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"missing workflow step: {name}")
