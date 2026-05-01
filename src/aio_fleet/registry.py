from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404
from dataclasses import dataclass

from aio_fleet.manifest import RepoConfig
from aio_fleet.release import (
    find_release_target_commit,
    latest_changelog_version,
    read_upstream_version,
)


@dataclass(frozen=True)
class RegistryTagSet:
    dockerhub: list[str]
    ghcr: list[str]
    upstream_version: str
    release_package_tag: str

    @property
    def all_tags(self) -> list[str]:
        return [*self.dockerhub, *self.ghcr]


def compute_registry_tags(
    repo: RepoConfig,
    *,
    sha: str,
    component: str = "aio",
    ghcr_image_name: str | None = None,
) -> RegistryTagSet:
    image_name = _component_image_name(repo, component)
    dockerhub_image = image_name.lower()
    ghcr_image = (ghcr_image_name or f"ghcr.io/{image_name}").lower()
    upstream_version = _read_component_upstream_version(repo, component)
    release_package_tag = _release_package_tag(repo, sha=sha, component=component)

    dockerhub_tags = [f"{dockerhub_image}:latest"]
    ghcr_tags = [f"{ghcr_image}:latest"]
    if upstream_version:
        dockerhub_tags.append(f"{dockerhub_image}:{upstream_version}")
        ghcr_tags.append(f"{ghcr_image}:{upstream_version}")
        if release_package_tag:
            dockerhub_tags.append(f"{dockerhub_image}:{release_package_tag}")
            ghcr_tags.append(f"{ghcr_image}:{release_package_tag}")
    dockerhub_tags.append(f"{dockerhub_image}:sha-{sha}")
    ghcr_tags.append(f"{ghcr_image}:sha-{sha}")
    return RegistryTagSet(
        dockerhub=dockerhub_tags,
        ghcr=ghcr_tags,
        upstream_version=upstream_version,
        release_package_tag=release_package_tag,
    )


def verify_registry_tags(tags: list[str]) -> list[str]:
    docker = shutil.which("docker")
    if docker is None:
        return ["docker CLI is required to verify registry tags"]
    failures: list[str] = []
    for tag in tags:
        result = subprocess.run(  # nosec B603
            [docker, "buildx", "imagetools", "inspect", tag],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            failures.append(f"{tag}: {detail or 'inspect failed'}")
    return failures


def _component_image_name(repo: RepoConfig, component: str) -> str:
    if component == "agent" and repo.is_signoz_suite:
        return str(repo.raw["components"]["agent"]["image_name"])
    return repo.image_name


def _read_component_upstream_version(repo: RepoConfig, component: str) -> str:
    try:
        if component == "agent" and repo.is_signoz_suite:
            agent = repo.raw["components"]["agent"]
            return read_upstream_version(
                repo.path / str(agent["dockerfile"]),
                repo.path / "components" / "signoz-agent" / "upstream.toml",
                version_key=str(agent.get("upstream_version_key", "UPSTREAM_VERSION")),
            )
        return read_upstream_version(
            repo.path / "Dockerfile",
            repo.path / "upstream.toml",
            version_key=str(repo.get("upstream_version_key", "UPSTREAM_VERSION")),
        )
    except (Exception, SystemExit):
        return ""


def _release_package_tag(repo: RepoConfig, *, sha: str, component: str) -> str:
    try:
        changelog_version = latest_changelog_version(
            repo.path / "CHANGELOG.md", semver=repo.publish_profile == "template"
        )
    except (Exception, SystemExit):
        return ""
    try:
        release_target_commit = find_release_target_commit(repo.path, changelog_version)
    except Exception:
        release_target_commit = ""
    if release_target_commit != sha:
        return ""

    upstream_version = _read_component_upstream_version(repo, component)
    match = re.match(rf"^{re.escape(upstream_version)}-aio\.(\d+)$", changelog_version)
    if not match:
        return changelog_version if repo.publish_profile == "changelog-version" else ""

    revision = match.group(1)
    if repo.publish_profile == "upstream-aio-track":
        return f"{upstream_version}-aio-v{revision}"
    return changelog_version
