from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "control-plane.yml"
SECRET_ENV_KEYS = {
    "AIO_FLEET_APP_ID",
    "AIO_FLEET_APP_INSTALLATION_ID",
    "AIO_FLEET_APP_PRIVATE_KEY",
    "AIO_FLEET_CHECK_TOKEN",
    "AIO_FLEET_GHCR_TOKEN",
    "AIO_FLEET_KUMA_PUSH_URL",
    "AIO_FLEET_ALERT_WEBHOOK_URL",
    "GH_TOKEN",
    "GITHUB_TOKEN",
}


def test_poll_checks_job_does_not_export_secrets_to_app_code_step() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    poll_checks = workflow["jobs"]["poll-checks"]

    assert not SECRET_ENV_KEYS.intersection(poll_checks.get("env", {}))  # nosec B101

    run_step = _step(poll_checks, "Run central control check")
    assert run_step.get("continue-on-error") is True  # nosec B101
    assert not SECRET_ENV_KEYS.intersection(run_step.get("env", {}))  # nosec B101
    assert "--check-run" not in run_step["run"]  # nosec B101


def test_app_code_checkouts_do_not_persist_credentials() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())

    for job_name in ("control-plane", "poll-checks"):
        job = workflow["jobs"][job_name]
        checkout = _step(job, "Checkout app repo")

        assert checkout["with"]["persist-credentials"] is False  # nosec B101


def test_app_code_checkouts_fetch_submodules_recursively() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())

    for job_name in ("control-plane", "poll-checks"):
        job = workflow["jobs"][job_name]
        checkout = _step(job, "Checkout app repo")

        assert checkout["with"]["submodules"] == "recursive"  # nosec B101


def test_control_check_steps_gate_publish_explicitly() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())

    manual = _step(workflow["jobs"]["control-plane"], "Run central control check")
    poll = _step(workflow["jobs"]["poll-checks"], "Run central control check")

    assert 'if [[ "${PUBLISH}" == "true" ]]' in manual["run"]  # nosec B101
    assert "args+=(--publish)" in manual["run"]  # nosec B101
    assert 'if [[ "${TARGET_PUBLISH}" == "true" ]]' in poll["run"]  # nosec B101
    assert "args+=(--publish)" in poll["run"]  # nosec B101
    assert "--no-integration" in poll["run"]  # nosec B101


def test_workflow_installs_central_dependencies_before_app_checks() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())

    for job_name in ("control-plane", "poll-checks"):
        job = workflow["jobs"][job_name]
        install = _step(job, "Install aio-fleet")
        run_check = _step(job, "Run central control check")

        assert install["run"] == 'python -m pip install -e ".[dev]"'  # nosec B101
        assert "python -m aio_fleet" in run_check["run"]  # nosec B101
        assert "control-check" in run_check["run"]  # nosec B101


def test_dashboard_update_receives_alert_env_without_app_check_leakage() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    dashboard = _step(workflow["jobs"]["control-plane"], "Update fleet dashboard issue")
    dashboard_env = dashboard["env"]

    assert (  # nosec B101
        dashboard_env["AIO_FLEET_KUMA_PUSH_URL"]
        == "${{ secrets.AIO_FLEET_KUMA_PUSH_URL }}"
    )
    assert (  # nosec B101
        dashboard_env["AIO_FLEET_ALERT_WEBHOOK_URL"]
        == "${{ secrets.AIO_FLEET_ALERT_WEBHOOK_URL }}"
    )

    manual_run = _step(workflow["jobs"]["control-plane"], "Run central control check")
    poll_run = _step(workflow["jobs"]["poll-checks"], "Run central control check")
    assert "AIO_FLEET_ALERT_WEBHOOK_URL" not in manual_run.get("env", {})  # nosec B101
    assert "AIO_FLEET_ALERT_WEBHOOK_URL" not in poll_run.get("env", {})  # nosec B101


def _step(job: dict[str, object], name: str) -> dict[str, object]:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"missing workflow step: {name}")
