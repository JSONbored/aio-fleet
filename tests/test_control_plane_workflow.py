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
    "AIO_FLEET_DASHBOARD_TOKEN",
    "AIO_FLEET_GHCR_TOKEN",
    "AIO_FLEET_ISSUE_TOKEN",
    "AIO_FLEET_KUMA_PUSH_URL",
    "AIO_FLEET_RELEASE_TOKEN",
    "AIO_FLEET_ALERT_WEBHOOK_URL",
    "AIO_FLEET_UPSTREAM_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
}
APP_CODE_SECRET_ENV_KEYS = SECRET_ENV_KEYS - {
    "AIO_FLEET_GHCR_TOKEN",
}
REGISTRY_PUBLISH_ENV_KEYS = {
    "DOCKERHUB_USERNAME",
    "DOCKERHUB_TOKEN",
    "AIO_FLEET_GHCR_TOKEN",
    "AIO_FLEET_GHCR_USERNAME",
}


def test_poll_checks_job_does_not_export_secrets_to_app_code_step() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    poll_checks = workflow["jobs"]["poll-checks"]

    assert not SECRET_ENV_KEYS.intersection(poll_checks.get("env", {}))  # nosec B101

    run_step = _step(poll_checks, "Run central control check")
    assert run_step.get("continue-on-error") is True  # nosec B101
    assert not APP_CODE_SECRET_ENV_KEYS.intersection(  # nosec B101
        run_step.get("env", {})
    )
    assert "--check-run" not in run_step["run"]  # nosec B101


def test_upstream_monitor_scopes_git_auth_without_standard_tokens() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    monitor = _step(workflow["jobs"]["control-plane"], "Monitor upstream releases")

    assert "AIO_FLEET_WORKFLOW_TOKEN" in monitor["env"]  # nosec B101
    assert "AIO_FLEET_CHECK_TOKEN" in monitor["env"]  # nosec B101
    assert "GH_TOKEN" not in monitor["env"]  # nosec B101
    assert "GITHUB_TOKEN" not in monitor["env"]  # nosec B101
    assert "workflow upstream-monitor" in monitor["run"]  # nosec B101
    assert "extraheader=AUTHORIZATION" not in monitor["run"]  # nosec B101
    assert '"config",' not in monitor["run"]  # nosec B101


def test_dashboard_checkout_does_not_put_auth_header_in_git_argv() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    dashboard = _step(workflow["jobs"]["control-plane"], "Checkout dashboard repos")

    assert "workflow checkout-dashboard" in dashboard["run"]  # nosec B101
    assert "extraheader=AUTHORIZATION" not in dashboard["run"]  # nosec B101
    assert '"config",' not in dashboard["run"]  # nosec B101


def test_dashboard_update_scopes_dashboard_tokens() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    dashboard = _step(workflow["jobs"]["control-plane"], "Update fleet dashboard issue")

    assert "AIO_FLEET_DASHBOARD_TOKEN" in dashboard["env"]  # nosec B101
    assert "AIO_FLEET_UPSTREAM_TOKEN" in dashboard["env"]  # nosec B101
    assert "AIO_FLEET_ISSUE_TOKEN" in dashboard["env"]  # nosec B101
    assert "APP_TOKEN" not in dashboard["env"]  # nosec B101
    assert "GH_TOKEN" not in dashboard["env"]  # nosec B101
    assert "GITHUB_TOKEN" not in dashboard["env"]  # nosec B101


def test_alert_test_mode_uses_alert_webhook_secret_only() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    on_config = workflow.get("on", workflow.get(True))
    mode = on_config["workflow_dispatch"]["inputs"]["mode"]
    app_token = _step(workflow["jobs"]["control-plane"], "Resolve GitHub App token")
    alert_test = _step(workflow["jobs"]["control-plane"], "Test alert webhook")

    assert "alert-test" in mode["options"]  # nosec B101
    assert "inputs.mode != 'alert-test'" in app_token["if"]  # nosec B101
    assert (  # nosec B101
        alert_test["if"]
        == "${{ github.event_name == 'workflow_dispatch' && inputs.mode == 'alert-test' }}"
    )
    assert alert_test["env"] == {  # nosec B101
        "AIO_FLEET_ALERT_WEBHOOK_URL": "${{ secrets.AIO_FLEET_ALERT_WEBHOOK_URL }}",
        "DETAILS_URL": "https://github.com/${{ github.repository }}/actions/runs/${{ github.run_id }}",
    }
    assert (
        "alert doctor --require-alerts --format json" in alert_test["run"]
    )  # nosec B101
    assert "alert test" in alert_test["run"]  # nosec B101
    assert "aio-fleet Discord alert test" in alert_test["run"]  # nosec B101
    assert "--dry-run" not in alert_test["run"]  # nosec B101


def test_dockerhub_tag_cleanup_mode_is_guarded() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    on_config = workflow.get("on", workflow.get(True))
    inputs = on_config["workflow_dispatch"]["inputs"]
    mode = inputs["mode"]
    cleanup = _step(workflow["jobs"]["control-plane"], "Delete Docker Hub tags")

    assert len(inputs) <= 10  # nosec B101
    assert "dockerhub-tag-cleanup" in mode["options"]  # nosec B101
    assert "dockerhub_image" in inputs  # nosec B101
    assert "dockerhub_tags" in inputs  # nosec B101
    assert cleanup["if"] == (  # nosec B101
        "${{ github.event_name == 'workflow_dispatch' && "
        "inputs.mode == 'dockerhub-tag-cleanup' }}"
    )
    assert cleanup["env"]["DOCKERHUB_USERNAME"] == (  # nosec B101
        "${{ secrets.DOCKERHUB_USERNAME }}"
    )
    assert cleanup["env"]["DOCKERHUB_DELETE_TOKEN"] == (  # nosec B101
        "${{ secrets.DOCKERHUB_DELETE_TOKEN }}"
    )
    assert "registry preflight" in cleanup["run"]  # nosec B101
    assert "--check-delete-scope" in cleanup["run"]  # nosec B101
    assert "registry delete-dockerhub-tags" in cleanup["run"]  # nosec B101
    assert "--required-substring" in cleanup["run"]  # nosec B101
    assert "--required-substring alpha" in cleanup["run"]  # nosec B101


def test_app_code_checkouts_do_not_persist_credentials() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())

    for job_name in ("control-plane", "poll-checks"):
        job = workflow["jobs"][job_name]
        checkout = _step(job, "Checkout app repo")

        assert checkout["with"]["persist-credentials"] is False  # nosec B101


def test_app_code_checkouts_disable_submodules_for_pull_requests() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())

    manual = _step(workflow["jobs"]["control-plane"], "Checkout app repo")
    poll = _step(workflow["jobs"]["poll-checks"], "Checkout app repo")

    assert "checkout_submodules" in manual["with"]["submodules"]  # nosec B101
    assert "inputs.event != 'pull_request'" in manual["with"]["submodules"]  # nosec B101
    assert "checkout_submodules" in poll["with"]["submodules"]  # nosec B101
    assert "matrix.target.event != 'pull_request'" in poll["with"]["submodules"]  # nosec B101


def test_control_check_steps_gate_publish_explicitly() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())

    manual = _step(workflow["jobs"]["control-plane"], "Run central control check")
    manual_preflight = _step(
        workflow["jobs"]["control-plane"], "Validate publish credentials"
    )
    poll = _step(workflow["jobs"]["poll-checks"], "Run central control check")
    poll_preflight = _step(
        workflow["jobs"]["poll-checks"], "Validate publish credentials"
    )

    assert "registry preflight --mode publish" in manual_preflight["run"]  # nosec B101
    assert 'if [[ "${PUBLISH}" == "true" ]]' in manual["run"]  # nosec B101
    assert "args+=(--publish --no-github-prereleases)" in manual["run"]  # nosec B101
    assert "AIO_FLEET_RELEASE_TOKEN" not in manual["env"]  # nosec B101
    assert "--report-json" in manual["run"]  # nosec B101
    assert "registry preflight --mode publish" in poll_preflight["run"]  # nosec B101
    assert 'if [[ "${TARGET_PUBLISH}" == "true" ]]' in poll["run"]  # nosec B101
    assert "args+=(--publish --no-github-prereleases)" in poll["run"]  # nosec B101
    assert "AIO_FLEET_RELEASE_TOKEN" not in poll["env"]  # nosec B101
    assert "--report-json" in poll["run"]  # nosec B101
    assert "args+=(--no-integration)" not in poll["run"]  # nosec B101


def test_github_prerelease_token_is_scoped_to_trusted_publish_step() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())

    manual_run = _step(workflow["jobs"]["control-plane"], "Run central control check")
    manual_release = _step(
        workflow["jobs"]["control-plane"], "Publish GitHub prereleases"
    )
    poll_run = _step(workflow["jobs"]["poll-checks"], "Run central control check")
    poll_release = _step(workflow["jobs"]["poll-checks"], "Publish GitHub prereleases")

    assert "AIO_FLEET_RELEASE_TOKEN" not in manual_run.get("env", {})  # nosec B101
    assert "AIO_FLEET_RELEASE_TOKEN" not in poll_run.get("env", {})  # nosec B101
    assert "AIO_FLEET_RELEASE_TOKEN" in manual_release["env"]  # nosec B101
    assert "AIO_FLEET_RELEASE_TOKEN" in poll_release["env"]  # nosec B101
    assert (
        "steps.central-control-check.outcome == 'success'" in manual_release["if"]
    )  # nosec B101
    assert (
        "steps.poll-central-control-check.outcome == 'success'" in poll_release["if"]
    )  # nosec B101
    assert "publish-github-prereleases" in manual_release["run"]  # nosec B101
    assert "publish-github-prereleases" in poll_release["run"]  # nosec B101


def test_publish_alert_steps_use_report_json_and_alert_secret() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())

    manual = _step(workflow["jobs"]["control-plane"], "Alert central publish status")
    poll = _step(workflow["jobs"]["poll-checks"], "Alert poll-check publish status")

    for step in (manual, poll):
        assert step["env"]["AIO_FLEET_ALERT_WEBHOOK_URL"] == (  # nosec B101
            "${{ secrets.AIO_FLEET_ALERT_WEBHOOK_URL }}"
        )
        assert "--event publish" in step["run"]  # nosec B101
        assert "--report-json" in step["run"]  # nosec B101
        assert "--details-url" in step["run"]  # nosec B101


def test_registry_credentials_are_not_logged_in_before_app_checks() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())

    for job_name in ("control-plane", "poll-checks"):
        job = workflow["jobs"][job_name]
        step_names = [step["name"] for step in job["steps"]]
        run_check = _step(job, "Run central control check")

        assert "Login to Docker Hub" not in step_names  # nosec B101
        assert "Login to GHCR" not in step_names  # nosec B101
        assert REGISTRY_PUBLISH_ENV_KEYS.issubset(set(run_check["env"]))  # nosec B101


def test_trunk_setup_actions_do_not_receive_job_scoped_secrets() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())

    for job_name in ("control-plane", "poll-checks"):
        job = workflow["jobs"][job_name]
        trunk_setup = _step(job, "Install Trunk")

        assert not SECRET_ENV_KEYS.intersection(job.get("env", {}))  # nosec B101
        assert "env" not in trunk_setup  # nosec B101


def test_workflow_installs_central_dependencies_before_app_checks() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())

    for job_name in ("control-plane", "poll-checks"):
        job = workflow["jobs"][job_name]
        install = _step(job, "Install aio-fleet")
        run_check = _step(job, "Run central control check")

        assert 'python -m pip install -e ".[dev]"' in install["run"]  # nosec B101
        assert "python -m aio_fleet" in run_check["run"]  # nosec B101
        assert "control-check" in run_check["run"]  # nosec B101


def test_workflow_forwards_component_scoped_publish() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())

    trigger = workflow.get("on") or workflow.get(True)
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert "publish_component" in inputs  # nosec B101

    manual_run = _step(workflow["jobs"]["control-plane"], "Run central control check")
    poll_run = _step(workflow["jobs"]["poll-checks"], "Run central control check")

    assert "PUBLISH_COMPONENT" in manual_run["env"]  # nosec B101
    assert "--publish-component" in manual_run["run"]  # nosec B101
    assert "TARGET_PUBLISH_COMPONENTS" in poll_run["env"]  # nosec B101
    assert "--publish-component" in poll_run["run"]  # nosec B101


def test_poll_failure_alert_uses_report_context_and_skips_publish_duplicates() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    poll = workflow["jobs"]["poll-checks"]
    failure = _step(poll, "Alert poll-check failure")
    run = failure["run"]

    assert "matrix.target.source" in poll["name"]  # nosec B101
    assert "TARGET_SOURCE" in failure["env"]  # nosec B101
    assert "TARGET_SHA" in failure["env"]  # nosec B101
    assert "CONTROL_REPORT" in failure["env"]  # nosec B101
    assert (  # nosec B101
        '"${TARGET_PUBLISH}" == "true" && -f "${CONTROL_REPORT}"' in run
    )
    assert "--report-json" in run  # nosec B101
    assert (  # nosec B101
        "poll-check:${TARGET_REPO}:${TARGET_SOURCE}:${TARGET_SHA}" in run
    )


def test_privileged_completion_restores_trusted_checkout_first() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())

    for job_name in ("control-plane", "poll-checks"):
        job = workflow["jobs"][job_name]
        restore = _step(job, "Restore trusted aio-fleet checkout")
        complete = _step(job, "Complete central control check")

        assert "AIO_FLEET_CHECK_TOKEN" not in restore.get("env", {})  # nosec B101
        assert "git reset --hard HEAD" in restore["run"]  # nosec B101
        assert "git clean -ffd -e app-repo/" in restore["run"]  # nosec B101
        assert (
            "python -m pip install --force-reinstall ." in restore["run"]
        )  # nosec B101
        assert "python -I -m aio_fleet check run" in complete["run"]  # nosec B101
        assert "RELEASE_OUTCOME" in complete["env"]  # nosec B101
        assert "GitHub prerelease publish failed" in complete["run"]  # nosec B101


def test_prerelease_publish_keeps_checked_out_app_repo() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())

    for job_name in ("control-plane", "poll-checks"):
        job = workflow["jobs"][job_name]
        restore = _step(job, "Restore trusted aio-fleet checkout")
        release = _step(job, "Publish GitHub prereleases")

        assert "git clean -ffd -e app-repo/" in restore["run"]  # nosec B101
        assert "--repo-path app-repo" in release["run"]  # nosec B101


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
