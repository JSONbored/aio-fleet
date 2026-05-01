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
        steps.append(install)
    generator = str(repo.get("generator_check_command", "") or "").strip()
    if generator:
        steps.append(Step("generator-check", shlex.split(generator), repo.path))
    unit_args = str(repo.get("unit_pytest_args", "") or "").strip()
    if unit_args:
        steps.append(
            Step(
                "unit-tests",
                [_repo_python(repo.path), "-m", "pytest", *shlex.split(unit_args)],
                repo.path,
            )
        )
    integration_args = str(repo.get("integration_pytest_args", "") or "").strip()
    if (
        include_integration
        and event in {"push", "release", "workflow_dispatch"}
        and integration_args
    ):
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
        steps.append(
            Step(
                "registry-publish",
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
                ],
                repo.path,
            )
        )
    return steps


def run_steps(steps: list[Step], *, dry_run: bool = False) -> list[str]:
    failures: list[str] = []
    for step in steps:
        if dry_run:
            print(
                f"{step.name}: {' '.join(shlex.quote(part) for part in step.command)}"
            )
            continue
        result = subprocess.run(  # nosec B603
            step.command,
            cwd=step.cwd,
            check=False,
            text=True,
            capture_output=True,
        )
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        if result.returncode != 0:
            failures.append(f"{step.name}: exit {result.returncode}")
            break
    return failures


def registry_publish_command(repo: RepoConfig, *, sha: str) -> list[str]:
    tags = compute_registry_tags(repo, sha=sha)
    command = [
        "docker",
        "buildx",
        "build",
        "--push",
        "--platform",
        str(repo.get("publish_platforms", "linux/amd64,linux/arm64")),
        "--cache-from",
        f"type=gha,scope={repo.get('docker_cache_scope')}",
        "--cache-to",
        f"type=gha,mode=max,scope={repo.get('docker_cache_scope')}",
    ]
    for tag in tags.all_tags:
        command.extend(["--tag", tag])
    command.append(".")
    return command


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
    requirements = repo_path / "requirements-dev.txt"
    if requirements.exists():
        return Step(
            "install-test-deps",
            [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
            repo_path,
        )
    if (repo_path / "tests").exists():
        return Step(
            "install-test-deps",
            [sys.executable, "-m", "pip", "install", "pytest"],
            repo_path,
        )
    return None
