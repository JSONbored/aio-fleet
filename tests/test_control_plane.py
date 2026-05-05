from __future__ import annotations

from pathlib import Path

from aio_fleet.control_plane import central_check_steps
from aio_fleet.manifest import RepoConfig, load_manifest

ROOT = Path(__file__).resolve().parents[1]


def test_central_check_steps_for_pr_skip_publish_and_integration(
    tmp_path: Path,
) -> None:
    repo = _repo_with_path(load_manifest(ROOT / "fleet.yml").repo("sure-aio"), tmp_path)
    (tmp_path / "tests").mkdir()

    steps = central_check_steps(repo, event="pull_request", include_trunk=False)

    names = [step.name for step in steps]
    assert names == [
        "validate-template-common",
        "install-test-deps",
    ]  # nosec B101
    assert str(ROOT) in steps[1].command[-1]  # nosec B101
    assert steps[1].command[-1].endswith("[app-tests]")  # nosec B101


def test_central_check_steps_for_push_include_integration_trunk_and_publish() -> None:
    repo = load_manifest(ROOT / "fleet.yml").repo("sure-aio")

    steps = central_check_steps(repo, event="push", publish=True)

    names = [step.name for step in steps]
    assert "build-pytest-image" in names  # nosec B101
    assert "integration-tests" in names  # nosec B101
    assert names[-2:] == ["trunk", "registry-publish"]  # nosec B101
    build = steps[names.index("build-pytest-image")]
    assert build.stream_output is True  # nosec B101
    assert build.command[:6] == [  # nosec B101
        "docker",
        "build",
        "--progress=plain",
        "--platform",
        "linux/amd64",
        "-t",
    ]
    integration = steps[names.index("integration-tests")]
    assert integration.env == {"AIO_PYTEST_USE_PREBUILT_IMAGE": "true"}  # nosec B101


def test_central_check_steps_for_push_without_publish_lets_tests_build_image() -> None:
    repo = load_manifest(ROOT / "fleet.yml").repo("sure-aio")

    steps = central_check_steps(repo, event="push", publish=False)

    names = [step.name for step in steps]
    assert "build-pytest-image" not in names  # nosec B101
    integration = steps[names.index("integration-tests")]
    assert integration.env is None  # nosec B101


def test_central_check_steps_publish_signoz_components() -> None:
    repo = load_manifest(ROOT / "fleet.yml").repo("signoz-aio")

    steps = central_check_steps(repo, event="workflow_dispatch", publish=True)

    publish_steps = [step for step in steps if step.name.startswith("registry-publish")]
    assert [step.name for step in publish_steps] == [  # nosec B101
        "registry-publish-aio",
        "registry-publish-agent",
    ]
    assert publish_steps[1].command[-2:] == ["--component", "agent"]  # nosec B101


def test_central_check_steps_can_skip_integration_for_poll_runs() -> None:
    repo = load_manifest(ROOT / "fleet.yml").repo("mem0-aio")

    steps = central_check_steps(repo, event="push", include_integration=False)

    names = [step.name for step in steps]
    assert "integration-tests" not in names  # nosec B101
    assert "unit-tests" in names  # nosec B101
    assert "trunk" in names  # nosec B101


def _repo_with_path(repo: RepoConfig, path: Path) -> RepoConfig:
    raw = dict(repo.raw)
    raw["path"] = str(path)
    return RepoConfig(name=repo.name, raw=raw, defaults=repo.defaults, owner=repo.owner)
