from __future__ import annotations

from pathlib import Path

import yaml

from aio_fleet.manifest import load_manifest
from aio_fleet.workflows import render_caller_workflow

ROOT = Path(__file__).resolve().parents[1]
PINNED_REF = "0123456789abcdef0123456789abcdef01234567"


def _render(repo_name: str) -> str:
    manifest = load_manifest(ROOT / "fleet.yml")
    return render_caller_workflow(manifest, manifest.repo(repo_name), PINNED_REF)


def _parse(text: str) -> dict[str, object]:
    return yaml.load(text, Loader=yaml.BaseLoader)


def test_rendered_callers_use_pinned_reusable_workflow() -> None:
    manifest = load_manifest(ROOT / "fleet.yml")

    for repo in manifest.repos.values():
        rendered = render_caller_workflow(manifest, repo, PINNED_REF)

        assert (  # nosec B101
            "uses: JSONbored/aio-fleet/.github/workflows/aio-build.yml@"
            f"{PINNED_REF}" in rendered
        )
        assert "@main" not in rendered  # nosec B101
        assert "secrets: inherit" in rendered  # nosec B101
        assert _parse(rendered)["jobs"]["aio-build"]["permissions"] == {  # type: ignore[index] # nosec B101
            "contents": "read",
            "packages": "write",
            "pull-requests": "write",
        }


def test_simple_app_caller_keeps_repo_specific_inputs() -> None:
    rendered = _render("sure-aio")

    assert "app_slug: sure-aio" in rendered  # nosec B101
    assert "image_name: jsonbored/sure-aio" in rendered  # nosec B101
    assert "docker_cache_scope: sure-aio-image" in rendered  # nosec B101
    assert "publish_profile: upstream-aio-track" in rendered  # nosec B101
    assert "checkout_submodules: false" in rendered  # nosec B101
    assert "agent_image_name:" not in rendered  # nosec B101


def test_mem0_caller_preserves_submodule_checkout_and_publish_paths() -> None:
    rendered = _render("mem0-aio")

    assert "checkout_submodules: true" in rendered  # nosec B101
    assert "openmemory" in rendered  # nosec B101
    assert "openmemory/**" in rendered  # nosec B101


def test_dify_caller_exposes_manual_extended_integration_input() -> None:
    rendered = _render("dify-aio")

    assert "run_extended_integration:" in rendered  # nosec B101
    assert "type: boolean" in rendered  # nosec B101
    assert "run_extended_integration: ${{ github.event_name == 'workflow_dispatch'" in rendered  # nosec B101
    assert "extended_integration_pytest_args: tests/integration -m extended_integration" in rendered  # nosec B101
    assert "generator_check_command: python3 scripts/generate_dify_template.py --check" in rendered  # nosec B101


def test_signoz_caller_keeps_component_publish_inputs() -> None:
    rendered = _render("signoz-aio")

    assert "publish_profile: signoz-suite" in rendered  # nosec B101
    assert "publish_platforms: linux/amd64" in rendered  # nosec B101
    assert "upstream_digest_arg: UPSTREAM_SIGNOZ_DIGEST" in rendered  # nosec B101
    assert "agent_image_name: jsonbored/signoz-agent" in rendered  # nosec B101
    assert "agent_context: components/signoz-agent" in rendered  # nosec B101
    assert "agent_dockerfile: components/signoz-agent/Dockerfile" in rendered  # nosec B101
    assert "agent_integration_pytest_args: tests/integration_agent -m integration" in rendered  # nosec B101

