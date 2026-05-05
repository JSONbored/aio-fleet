from __future__ import annotations

import os
import shlex
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from aio_fleet.manifest import RepoConfig
from aio_fleet.registry import compute_registry_tags


@dataclass(frozen=True)
class Step:
    name: str
    command: list[str]
    cwd: Path
    env: dict[str, str] | None = None
    stream_output: bool = False
    timeout_seconds: int | None = None
    inherit_secrets: bool = True


def central_check_steps(
    repo: RepoConfig,
    *,
    event: str,
    manifest_path: Path | None = None,
    publish: bool = False,
    include_trunk: bool = True,
    include_integration: bool = True,
) -> list[Step]:
    manifest_args = ["--manifest", str(manifest_path)] if manifest_path else []
    steps = [
        Step(
            "validate-template-common",
            [
                sys.executable,
                "-m",
                "aio_fleet.cli",
                *manifest_args,
                "validate-template-common",
                "--repo",
                repo.name,
                "--repo-path",
                str(repo.path),
            ],
            repo.path,
        )
    ]
    install = _install_test_dependencies_step(repo.path)
    if install is not None:
        steps.append(Step(**{**install.__dict__, "inherit_secrets": False}))
    generator = str(repo.get("generator_check_command", "") or "").strip()
    if generator:
        steps.append(
            Step("generator-check", shlex.split(generator), repo.path, inherit_secrets=False)
        )
    unit_args = str(repo.get("unit_pytest_args", "") or "").strip()
    if unit_args:
        steps.append(
            Step(
                "unit-tests",
                [_repo_python(repo.path), "-m", "pytest", *shlex.split(unit_args)],
                repo.path,
                inherit_secrets=False,
            )
        )
    integration_args = str(repo.get("integration_pytest_args", "") or "").strip()
    prebuilt_integration_image = False
    if (
        include_integration
        and event in {"push", "release", "workflow_dispatch"}
        and integration_args
    ):
        if publish:
            steps.append(_pytest_image_build_step(repo))
            prebuilt_integration_image = True
        steps.append(
            Step(
                "integration-tests",
                [
                    _repo_python(repo.path),
                    "-m",
                    "pytest",
                    *shlex.split(integration_args),
                ],
                repo.path,
                env=(
                    {"AIO_PYTEST_USE_PREBUILT_IMAGE": "true"}
                    if prebuilt_integration_image
                    else None
                ),
                timeout_seconds=_repo_timeout_seconds(
                    repo, "integration_timeout_seconds", default=1800
                ),
                inherit_secrets=False,
            )
        )
    if include_trunk:
        steps.append(
            Step(
                "trunk",
                [
                    sys.executable,
                    "-m",
                    "aio_fleet.cli",
                    *manifest_args,
                    "trunk",
                    "run",
                    "--repo",
                    repo.name,
                    "--repo-path",
                    str(repo.path),
                    "--no-fix",
                ],
                repo.path,
            )
        )
    if publish:
        components = publish_components(repo)
        for component in components:
            step_name = (
                "registry-publish"
                if components == ["aio"]
                else f"registry-publish-{component}"
            )
            steps.append(
                Step(
                    step_name,
                    [
                        sys.executable,
                        "-m",
                        "aio_fleet.cli",
                        *manifest_args,
                        "registry",
                        "publish",
                        "--repo",
                        repo.name,
                        "--repo-path",
                        str(repo.path),
                        "--component",
                        component,
                    ],
                    repo.path,
                    stream_output=True,
                    timeout_seconds=_repo_timeout_seconds(
                        repo, "registry_publish_timeout_seconds", default=3600
                    ),
                )
            )
    return steps


def run_steps(steps: list[Step], *, dry_run: bool = False) -> list[str]:
    failures: list[str] = []
    for step in steps:
        if dry_run:
            env_prefix = ""
            if step.env:
                env_prefix = " ".join(
                    f"{key}={shlex.quote(value)}"
                    for key, value in sorted(step.env.items())
                )
                env_prefix += " "
            print(
                f"{step.name}: {env_prefix}"
                f"{' '.join(shlex.quote(part) for part in step.command)}"
            )
            continue
        env = dict(os.environ) if step.inherit_secrets else _scrubbed_env()
        if step.env:
            env.update(step.env)
        if step.stream_output:
            try:
                result = subprocess.run(  # nosec B603
                    step.command,
                    cwd=step.cwd,
                    check=False,
                    text=True,
                    env=env,
                    timeout=step.timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                timeout = step.timeout_seconds or 0
                failures.append(f"{step.name}: timed out after {timeout}s")
                break
            if result.returncode != 0:
                failures.append(f"{step.name}: exit {result.returncode}")
                break
            continue
        try:
            result = subprocess.run(  # nosec B603
                step.command,
                cwd=step.cwd,
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=step.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            if exc.stdout:
                print(exc.stdout, end="")
            if exc.stderr:
                print(exc.stderr, file=sys.stderr, end="")
            timeout = step.timeout_seconds or 0
            failures.append(f"{step.name}: timed out after {timeout}s")
            break
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        if result.returncode != 0:
            failures.append(f"{step.name}: exit {result.returncode}")
            break
    return failures


def _scrubbed_env() -> dict[str, str]:
    blocked_prefixes = ("AIO_FLEET_",)
    blocked_keys = {
        "APP_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "DOCKERHUB_TOKEN",
        "CR_PAT",
    }
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in blocked_keys:
            continue
        if any(key.startswith(prefix) for prefix in blocked_prefixes):
            continue
        env[key] = value
    return env


def _pytest_image_build_step(repo: RepoConfig) -> Step:
    image_tag = str(repo.get("pytest_image_tag", "") or "").strip()
    if not image_tag:
        raise ValueError(f"{repo.name} is missing pytest_image_tag")
    platform = str(repo.get("pytest_image_platform", "linux/amd64") or "linux/amd64")
    dockerfile = str(repo.get("pytest_dockerfile", "Dockerfile") or "Dockerfile")
    context = str(repo.get("pytest_build_context", ".") or ".")
    command = [
        "docker",
        "build",
        "--progress=plain",
        "--platform",
        platform,
        "-t",
        image_tag,
    ]
    if dockerfile != "Dockerfile":
        command.extend(["-f", dockerfile])
    command.append(context)
    return Step(
        "build-pytest-image",
        command,
        repo.path,
        stream_output=True,
        timeout_seconds=_repo_timeout_seconds(
            repo, "pytest_image_build_timeout_seconds", default=1800
        ),
    )


def _repo_timeout_seconds(repo: RepoConfig, key: str, *, default: int) -> int:
    value = repo.get(key, default)
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = default
    return max(timeout, 1)


def publish_components(repo: RepoConfig) -> list[str]:
    components = repo.raw.get("components")
    if not isinstance(components, dict):
        return ["aio"]
    names = [
        name
        for name, config in components.items()
        if name == "aio" or (isinstance(config, dict) and config.get("image_name"))
    ]
    return names or ["aio"]


def registry_publish_command(
    repo: RepoConfig, *, sha: str, component: str = "aio"
) -> list[str]:
    tags = compute_registry_tags(repo, sha=sha, component=component)
    component_config = _component_config(repo, component)
    cache_scope = component_config.get(
        "docker_cache_scope", repo.get("docker_cache_scope")
    )
    platforms = component_config.get(
        "publish_platforms", repo.get("publish_platforms", "linux/amd64,linux/arm64")
    )
    command = [
        "docker",
        "buildx",
        "build",
        "--push",
        "--platform",
        str(platforms),
        "--cache-from",
        f"type=gha,scope={cache_scope}",
        "--cache-to",
        f"type=gha,mode=max,scope={cache_scope}",
    ]
    dockerfile = component_config.get("dockerfile")
    if dockerfile:
        command.extend(["--file", str(dockerfile)])
    for tag in tags.all_tags:
        command.extend(["--tag", tag])
    command.append(str(component_config.get("context", ".")))
    return command


def _component_config(repo: RepoConfig, component: str) -> dict[str, object]:
    components = repo.raw.get("components")
    if isinstance(components, dict):
        config = components.get(component)
        if isinstance(config, dict):
            return config
    return {}


def run_central_trunk(
    repo: RepoConfig, *, fix: bool = False
) -> subprocess.CompletedProcess[str]:
    trunk = os.environ.get("TRUNK_PATH") or shutil.which("trunk")
    if trunk is None:
        return subprocess.CompletedProcess(
            ["trunk"], 127, "", "trunk CLI is not installed"
        )
    git = shutil.which("git")
    if git is None:
        return subprocess.CompletedProcess(["git"], 127, "", "git CLI is not installed")
    aio_root = Path(__file__).resolve().parents[2]
    central_trunk = aio_root / ".trunk"
    tmp_root = Path(os.environ.get("AIO_FLEET_TMPDIR") or tempfile.gettempdir())
    tmp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{repo.name}-trunk-", dir=tmp_root) as tmp:
        scratch = Path(tmp) / repo.name
        subprocess.run(
            [git, "clone", "--quiet", str(repo.path), str(scratch)], check=True
        )  # nosec B603
        scratch_trunk = scratch / ".trunk"
        if scratch_trunk.exists():
            shutil.rmtree(scratch_trunk)
        scratch_trunk.mkdir()
        shutil.copy2(central_trunk / "trunk.yaml", scratch_trunk / "trunk.yaml")
        if (central_trunk / "configs").exists():
            shutil.copytree(central_trunk / "configs", scratch_trunk / "configs")
        command = [
            trunk,
            "check",
            "--show-existing",
            "--all",
            "--no-progress",
            "--color=false",
            "--fix" if fix else "--no-fix",
        ]
        env = dict(os.environ)
        env.setdefault("FORCE_COLOR", "0")
        return subprocess.run(  # nosec B603
            command, cwd=scratch, check=False, text=True, capture_output=True, env=env
        )


def _repo_python(repo_path: Path) -> str:
    for candidate in (
        repo_path / ".venv" / "bin" / "python",
        repo_path / ".venv" / "bin" / "python3",
    ):
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return sys.executable


def _install_test_dependencies_step(repo_path: Path) -> Step | None:
    if (repo_path / "tests").exists():
        aio_root = Path(__file__).resolve().parents[2]
        return Step(
            "install-test-deps",
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-e",
                f"{aio_root}[app-tests]",
            ],
            repo_path,
        )
    return None
