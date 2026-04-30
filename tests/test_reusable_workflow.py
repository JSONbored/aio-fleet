from __future__ import annotations

from pathlib import Path

import yaml

from aio_fleet.validators import pinned_action_failures

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/aio-build.yml"
REUSABLE_WORKFLOWS = sorted((ROOT / ".github/workflows").glob("aio-*.yml"))


def _workflow_text() -> str:
    return WORKFLOW.read_text()


def _workflow() -> dict[str, object]:
    return yaml.load(_workflow_text(), Loader=yaml.BaseLoader)  # nosec B506


def test_reusable_workflow_defines_expected_workflow_call_inputs() -> None:
    workflow = _workflow()
    inputs = workflow["on"]["workflow_call"]["inputs"]  # type: ignore[index]

    for required_input in [
        "app_slug",
        "image_name",
        "docker_cache_scope",
        "pytest_image_tag",
        "publish_profile",
        "checkout_submodules",
        "xml_paths",
        "catalog_assets",
        "catalog_published",
    ]:
        assert required_input in inputs  # nosec B101


def test_reusable_workflow_owns_ci_gate_flags_centrally() -> None:
    text = _workflow_text()

    assert "scripts/ci_flags.py" not in text  # nosec B101
    assert "push:refs/heads/main)" in text  # nosec B101
    assert "pull_request:*|workflow_dispatch:*)" in text  # nosec B101
    assert "publish_requested=true" in text  # nosec B101
    assert "publish_requested=false" in text  # nosec B101


def test_reusable_build_workflow_owns_pytest_upload_centrally() -> None:
    text = _workflow_text()

    assert "uses: ./.github/actions/run-pytest" not in text  # nosec B101
    assert (
        "trunk-io/analytics-uploader@95a0fb8b29e45b6068304261fb518644b426a803" in text
    )  # nosec B101
    assert "reports/pytest-unit.xml" in text  # nosec B101
    assert "reports/pytest-integration.xml" in text  # nosec B101
    assert "reports/pytest-agent-integration.xml" in text  # nosec B101
    assert "reports/pytest-extended-integration.xml" in text  # nosec B101


def test_reusable_build_workflow_validates_caller_drift_centrally() -> None:
    text = _workflow_text()

    assert "Verify caller workflow drift" in text  # nosec B101
    assert "python -m aio_fleet.cli" in text  # nosec B101
    assert '--repo "${{ inputs.app_slug }}"' in text  # nosec B101
    assert "--repo-path ." in text  # nosec B101


def test_reusable_build_workflow_runs_fleet_policy_validator() -> None:
    text = _workflow_text()

    assert "Validate fleet repo policy" in text  # nosec B101
    assert "validate-repo" in text  # nosec B101
    assert "validate-actions --repo-path ." in text  # nosec B101


def test_reusable_build_workflow_installs_fleet_validator_before_unit_tests() -> None:
    text = _workflow_text()

    unit_job = text.split("  unit-tests:", 1)[1].split("  integration-tests:", 1)[0]
    assert "Checkout aio-fleet validator" in unit_job  # nosec B101
    assert "Install aio-fleet validator" in unit_job  # nosec B101
    assert unit_job.index("Install aio-fleet validator") < unit_job.index(
        "Run unit and template tests"
    )  # nosec B101


def test_reusable_build_workflow_uses_fleet_catalog_sync() -> None:
    text = _workflow_text()

    assert "sync-catalog" in text  # nosec B101
    assert "--repo-path ." in text  # nosec B101
    assert "CATALOG_PUBLISHED" in text  # nosec B101


def test_reusable_workflow_preserves_submodule_checkout_for_repos_that_need_it() -> (
    None
):
    text = _workflow_text()

    assert "checkout_submodules:" in text  # nosec B101
    assert (
        text.count("submodules: ${{ inputs.checkout_submodules }}") >= 7
    )  # nosec B101


def test_reusable_workflow_keeps_publish_gates_behind_integration_success() -> None:
    text = _workflow_text()

    assert "needs.integration-tests.result == 'success'" in text  # nosec B101
    assert "needs.agent-integration-tests.result == 'success'" in text  # nosec B101
    assert "github.event_name == 'push'" in text  # nosec B101
    assert "github.ref == 'refs/heads/main'" in text  # nosec B101
    assert "publish_requested == 'true'" in text  # nosec B101


def test_reusable_workflow_keeps_known_component_exceptions_explicit() -> None:
    text = _workflow_text()

    assert "inputs.publish_profile == 'signoz-suite'" in text  # nosec B101
    assert "UPSTREAM_DIFY_API_DIGEST" in text  # nosec B101
    assert "UPSTREAM_DIFY_PLUGIN_DAEMON_DIGEST" in text  # nosec B101
    assert "UPSTREAM_OTELCOL_DIGEST" in text  # nosec B101


def test_reusable_workflow_mirrors_docker_hub_from_ghcr() -> None:
    text = _workflow_text()

    assert "Build and push GHCR image" in text  # nosec B101
    assert "Build and push GHCR agent image" in text  # nosec B101
    assert "tags: ${{ steps.prep.outputs.ghcr_tags }}" in text  # nosec B101
    assert "tags: ${{ steps.prep.outputs.tags }}" not in text  # nosec B101
    assert "dockerhub_tags<<EOF" in text  # nosec B101
    assert "skopeo copy --all --retry-times 3" in text  # nosec B101
    assert "skopeo inspect --raw" in text  # nosec B101


def test_nonlocal_actions_are_pinned_to_full_commit_shas() -> None:
    assert pinned_action_failures(ROOT) == []  # nosec B101


def test_aio_fleet_ci_runs_actionlint_without_external_integrations() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text()

    assert "-shellcheck ''" in text  # nosec B101
    assert "-pyflakes ''" in text  # nosec B101


def test_release_and_upstream_reusable_workflows_exist() -> None:
    names = {path.name for path in REUSABLE_WORKFLOWS}

    assert "aio-build.yml" in names  # nosec B101
    assert "aio-check-upstream.yml" in names  # nosec B101
    assert "aio-prepare-release.yml" in names  # nosec B101
    assert "aio-publish-release.yml" in names  # nosec B101
