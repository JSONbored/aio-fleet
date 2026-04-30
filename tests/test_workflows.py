from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import yaml

from aio_fleet.cli import cmd_verify_caller
from aio_fleet.manifest import RepoConfig, load_manifest
from aio_fleet.workflows import (
    rendered_workflows,
    render_caller_workflow,
)

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


def test_rendered_release_and_upstream_callers_use_pinned_reusable_workflows() -> None:
    manifest = load_manifest(ROOT / "fleet.yml")

    for repo in manifest.repos.values():
        rendered = rendered_workflows(manifest, repo, PINNED_REF)
        text = "\n".join(rendered.values())

        assert (  # nosec B101
            "uses: JSONbored/aio-fleet/.github/workflows/aio-check-upstream.yml@"
            f"{PINNED_REF}" in text
        )
        assert (  # nosec B101
            "uses: JSONbored/aio-fleet/.github/workflows/aio-prepare-release.yml@"
            f"{PINNED_REF}" in text
        )
        assert (  # nosec B101
            "uses: JSONbored/aio-fleet/.github/workflows/aio-publish-release.yml@"
            f"{PINNED_REF}" in text
        )
        assert "@main" not in text  # nosec B101


def test_simple_app_caller_keeps_repo_specific_inputs() -> None:
    rendered = _render("sure-aio")

    assert "app_slug: sure-aio" in rendered  # nosec B101
    assert "image_name: jsonbored/sure-aio" in rendered  # nosec B101
    assert "docker_cache_scope: sure-aio-image" in rendered  # nosec B101
    assert "publish_profile: upstream-aio-track" in rendered  # nosec B101
    assert "checkout_submodules: false" in rendered  # nosec B101
    assert 'extended_integration_pytest_args: ""' in rendered  # nosec B101
    assert 'generator_check_command: ""' in rendered  # nosec B101
    assert "agent_image_name:" not in rendered  # nosec B101


def test_simplelogin_caller_is_amd64_only() -> None:
    rendered = _render("simplelogin-aio")

    assert "publish_platforms: linux/amd64\n" in rendered  # nosec B101
    assert "publish_platforms: linux/amd64,linux/arm64" not in rendered  # nosec B101


def test_mem0_caller_preserves_submodule_checkout_and_publish_paths() -> None:
    rendered = _render("mem0-aio")

    assert "checkout_submodules: true" in rendered  # nosec B101
    assert "openmemory" in rendered  # nosec B101
    assert "openmemory/**" in rendered  # nosec B101


def test_caller_paths_include_shared_boilerplate_surfaces() -> None:
    rendered = _render("sure-aio")

    assert ".github/**" in rendered  # nosec B101
    assert "docs/**" in rendered  # nosec B101
    assert "SECURITY.md" in rendered  # nosec B101


def test_dify_caller_exposes_manual_extended_integration_input() -> None:
    rendered = _render("dify-aio")

    assert "catalog_published: true" in rendered  # nosec B101
    assert "run_extended_integration:" in rendered  # nosec B101
    assert "type: boolean" in rendered  # nosec B101
    assert "run_extended_integration: ${{ github.event_name == 'workflow_dispatch'" in rendered  # nosec B101
    assert "extended_integration_pytest_args: tests/integration -m extended_integration" in rendered  # nosec B101
    assert "generator_check_command: python3 scripts/generate_dify_template.py --check" in rendered  # nosec B101


def test_signoz_caller_keeps_component_publish_inputs() -> None:
    rendered = _render("signoz-aio")
    parsed = _parse(rendered)
    inputs = parsed["jobs"]["aio-build"]["with"]  # type: ignore[index]

    assert "publish_profile: signoz-suite" in rendered  # nosec B101
    assert "publish_platforms: linux/amd64" in rendered  # nosec B101
    assert "upstream_digest_arg: UPSTREAM_SIGNOZ_DIGEST" in rendered  # nosec B101
    assert "agent_image_name: jsonbored/signoz-agent" in rendered  # nosec B101
    assert "agent_context: components/signoz-agent" in rendered  # nosec B101
    assert "agent_dockerfile: components/signoz-agent/Dockerfile" in rendered  # nosec B101
    assert "agent_integration_pytest_args: tests/integration_agent -m integration" in rendered  # nosec B101
    assert inputs["agent_image_name"] == "jsonbored/signoz-agent"  # nosec B101
    assert inputs["catalog_assets"].strip().endswith("assets/app-icon.png|icons/signoz.png")  # nosec B101


def test_signoz_release_callers_keep_component_lanes() -> None:
    manifest = load_manifest(ROOT / "fleet.yml")
    rendered = rendered_workflows(manifest, manifest.repo("signoz-aio"), PINNED_REF)
    text_by_name = {path.name: text for path, text in rendered.items()}

    assert "release-agent.yml" in text_by_name  # nosec B101
    assert "publish-release-agent.yml" in text_by_name  # nosec B101
    assert "component: signoz-aio" in text_by_name["release.yml"]  # nosec B101
    assert "component: signoz-aio" in text_by_name["publish-release.yml"]  # nosec B101
    assert "component: signoz-agent" in text_by_name["release-agent.yml"]  # nosec B101
    assert "component: signoz-agent" in text_by_name["publish-release-agent.yml"]  # nosec B101
    assert "component_matrix: '[\"signoz-aio\", \"signoz-agent\"]'" in text_by_name["check-upstream.yml"]  # nosec B101
    assert "components/signoz-agent/Dockerfile" in text_by_name["check-upstream.yml"]  # nosec B101


def test_template_release_uses_semver_release_tag_command() -> None:
    manifest = load_manifest(ROOT / "fleet.yml")
    rendered = rendered_workflows(manifest, manifest.repo("unraid-aio-template"), PINNED_REF)
    release_text = next(text for path, text in rendered.items() if path.name == "release.yml")

    assert "previous_tag_command: latest-release-tag" in release_text  # nosec B101


def _repo_in_tmp(repo_name: str, tmp_path: Path) -> RepoConfig:
    manifest = load_manifest(ROOT / "fleet.yml")
    repo = manifest.repo(repo_name)
    raw = dict(repo.raw)
    raw["path"] = str(tmp_path)
    return RepoConfig(name=repo.name, raw=raw, defaults=repo.defaults, owner=repo.owner)


def _write_rendered_workflows(repo_name: str, tmp_path: Path) -> None:
    manifest = load_manifest(ROOT / "fleet.yml")
    repo = _repo_in_tmp(repo_name, tmp_path)
    for path, text in rendered_workflows(manifest, repo, PINNED_REF).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_verify_caller_accepts_generated_workflows(tmp_path: Path) -> None:
    _write_rendered_workflows("sure-aio", tmp_path)

    result = cmd_verify_caller(
        Namespace(
            manifest=str(ROOT / "fleet.yml"),
            repo="sure-aio",
            repo_path=str(tmp_path),
            ref=PINNED_REF,
            diff=True,
        )
    )

    assert result == 0  # nosec B101


def test_verify_caller_rejects_drifted_workflows(
    tmp_path: Path,
    capsys,
) -> None:
    _write_rendered_workflows("sure-aio", tmp_path)
    (tmp_path / ".github" / "workflows" / "build.yml").write_text("name: drifted\n")

    result = cmd_verify_caller(
        Namespace(
            manifest=str(ROOT / "fleet.yml"),
            repo="sure-aio",
            repo_path=str(tmp_path),
            ref=PINNED_REF,
            diff=True,
        )
    )

    assert result == 1  # nosec B101
    assert "build.yml: out of date with aio-fleet manifest" in capsys.readouterr().err  # nosec B101
