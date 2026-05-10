from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass

from aio_fleet.manifest import RepoConfig
from aio_fleet.release import (
    find_release_target_commit,
    git,
    git_is_ancestor,
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


def verify_registry_tags(
    tags: list[str], *, env: Mapping[str, str] | None = None
) -> list[str]:
    docker = shutil.which("docker")
    if docker is None:
        return ["docker CLI is required to verify registry tags"]
    failures: list[str] = []
    for tag in tags:
        failure = (
            _verify_dockerhub_tag(docker, tag, env=env)
            if _is_dockerhub_tag(tag)
            else _verify_with_docker_imagetools(docker, tag, env=env)
        )
        if failure:
            failures.append(failure)
    return failures


def _verify_with_docker_imagetools(
    docker: str, tag: str, *, env: Mapping[str, str] | None = None
) -> str | None:
    result = subprocess.run(  # nosec B603
        [docker, "buildx", "imagetools", "inspect", tag],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )
    if result.returncode == 0:
        return None
    detail = (result.stderr or result.stdout).strip()
    return f"{tag}: {detail or 'inspect failed'}"


def _is_dockerhub_tag(tag: str) -> bool:
    image = tag.rsplit(":", 1)[0] if ":" in tag else tag
    first = image.split("/", 1)[0]
    return first in {"docker.io", "index.docker.io"} or "." not in first


def _verify_dockerhub_tag(
    docker: str,
    tag: str,
    *,
    env: Mapping[str, str] | None = None,
    attempts: int = 8,
) -> str | None:
    docker_failure = _verify_with_docker_imagetools(docker, tag, env=env)
    if docker_failure is None:
        return None

    parsed = _dockerhub_tag_parts(tag)
    if parsed is None:
        return f"{tag}: unsupported Docker Hub tag format"
    namespace, repository, tag_name = parsed
    quoted_tag = urllib.parse.quote(tag_name, safe="")
    url = (
        "https://hub.docker.com/v2/repositories/"
        f"{namespace}/{repository}/tags/{quoted_tag}"
    )
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=20) as response:  # nosec B310
                if response.status == 200:
                    json.load(response)
                    return None
                last_error = f"unexpected status {response.status}"
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            last_error = f"invalid Docker Hub JSON response: {error}"
        except urllib.error.HTTPError as error:
            if error.code == 404:
                last_error = "tag not found on Docker Hub"
            else:
                last_error = f"HTTP {error.code}: {error.reason}"
        except urllib.error.URLError as error:
            last_error = str(error.reason)
        if attempt < attempts:
            time.sleep(2 * attempt)
    if last_error == "tag not found on Docker Hub":
        return f"{tag}: {last_error}"
    return f"{tag}: Docker Hub tag lookup failed: {last_error or 'unknown error'}"


def _dockerhub_tag_parts(tag: str) -> tuple[str, str, str] | None:
    if ":" not in tag:
        return None
    image, tag_name = tag.rsplit(":", 1)
    parts = image.split("/")
    if parts and parts[0] in {"docker.io", "index.docker.io"}:
        parts = parts[1:]
    if len(parts) == 1:
        namespace, repository = "library", parts[0]
    elif len(parts) == 2:
        namespace, repository = parts
    else:
        return None
    if not namespace or not repository or not tag_name:
        return None
    return namespace, repository, tag_name


def _component_image_name(repo: RepoConfig, component: str) -> str:
    components = repo.raw.get("components")
    if isinstance(components, dict):
        config = components.get(component)
        if isinstance(config, dict) and config.get("image_name"):
            return str(config["image_name"])
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
    except (Exception, SystemExit):
        release_target_commit = ""
    if not _release_tag_sha_allowed(repo, release_target_commit, sha):
        return ""

    upstream_version = _read_component_upstream_version(repo, component)
    match = re.match(rf"^{re.escape(upstream_version)}-aio\.(\d+)$", changelog_version)
    if not match:
        return changelog_version if repo.publish_profile == "changelog-version" else ""

    revision = match.group(1)
    if repo.publish_profile == "upstream-aio-track":
        return f"{upstream_version}-aio.{revision}"
    return changelog_version


_RELEASE_FORMAT_SUBJECT = re.compile(
    r"^chore\(release\): format .+ changelog(?: \(#\d+\))?$"
)


def _release_tag_sha_allowed(
    repo: RepoConfig, release_target_commit: str, sha: str
) -> bool:
    if release_target_commit == sha:
        return True
    try:
        if not git_is_ancestor(repo.path, release_target_commit, sha):
            return False
        subjects = git(
            repo.path, "log", "--format=%s", f"{release_target_commit}..{sha}"
        )
    except (Exception, SystemExit):
        return False
    return bool(subjects.strip()) and all(
        _RELEASE_FORMAT_SUBJECT.match(subject.strip())
        for subject in subjects.splitlines()
        if subject.strip()
    )
