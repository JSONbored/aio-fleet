from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/aio-build.yml"
REUSABLE_WORKFLOWS = sorted((ROOT / ".github/workflows").glob("aio-*.yml"))


def _workflow_text() -> str:
    return WORKFLOW.read_text()


def _workflow() -> dict[str, object]:
    return yaml.load(_workflow_text(), Loader=yaml.BaseLoader)


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
    assert "trunk-io/analytics-uploader@95a0fb8b29e45b6068304261fb518644b426a803" in text  # nosec B101
    assert "reports/pytest-unit.xml" in text  # nosec B101
    assert "reports/pytest-integration.xml" in text  # nosec B101
    assert "reports/pytest-agent-integration.xml" in text  # nosec B101
    assert "reports/pytest-extended-integration.xml" in text  # nosec B101


def test_reusable_workflow_preserves_submodule_checkout_for_repos_that_need_it() -> None:
    text = _workflow_text()

    assert "checkout_submodules:" in text  # nosec B101
    assert text.count("submodules: ${{ inputs.checkout_submodules }}") >= 7  # nosec B101


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
    action_ref = re.compile(r"^\s*uses:\s*([^@\s]+)@([^\s#]+)", re.MULTILINE)
    failures = []

    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for match in action_ref.finditer(path.read_text()):
            target, ref = match.groups()
            if target.startswith("./"):
                continue
            if not re.fullmatch(r"[0-9a-f]{40}", ref):
                failures.append(f"{path.relative_to(ROOT)}: {target}@{ref}")

    assert failures == []  # nosec B101


def test_release_and_upstream_reusable_workflows_exist() -> None:
    names = {path.name for path in REUSABLE_WORKFLOWS}

    assert "aio-build.yml" in names  # nosec B101
    assert "aio-check-upstream.yml" in names  # nosec B101
    assert "aio-prepare-release.yml" in names  # nosec B101
    assert "aio-publish-release.yml" in names  # nosec B101
