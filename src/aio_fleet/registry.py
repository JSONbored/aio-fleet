from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from aio_fleet.changelog import component_config
from aio_fleet.manifest import RepoConfig
from aio_fleet.release import (
    find_release_target_commit,
    git,
    git_is_ancestor,
    latest_changelog_version,
    latest_component_changelog_version,
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
    version_tags_allowed = _version_tags_allowed(
        repo, component=component, release_package_tag=release_package_tag
    )
    include_upstream_version_tag = _component_bool(
        repo, component, "include_upstream_version_tag", True
    )
    include_sha_tag = _component_bool(repo, component, "include_sha_tag", True)

    dockerhub_tags = []
    ghcr_tags = []
    if version_tags_allowed:
        dockerhub_tags.extend(
            f"{dockerhub_image}:{tag}"
            for tag in _component_floating_tags(repo, component)
        )
        ghcr_tags.extend(
            f"{ghcr_image}:{tag}" for tag in _component_floating_tags(repo, component)
        )
    if version_tags_allowed and include_upstream_version_tag and upstream_version:
        dockerhub_tags.append(f"{dockerhub_image}:{upstream_version}")
        ghcr_tags.append(f"{ghcr_image}:{upstream_version}")
    if version_tags_allowed and release_package_tag:
        dockerhub_tags.append(f"{dockerhub_image}:{release_package_tag}")
        ghcr_tags.append(f"{ghcr_image}:{release_package_tag}")
    if include_sha_tag:
        dockerhub_tags.append(
            f"{dockerhub_image}:{_component_sha_tag(repo, component, sha)}"
        )
        ghcr_tags.append(f"{ghcr_image}:{_component_sha_tag(repo, component, sha)}")
    return RegistryTagSet(
        dockerhub=dockerhub_tags,
        ghcr=ghcr_tags,
        upstream_version=upstream_version,
        release_package_tag=release_package_tag,
    )


def component_registry_release_tag(repo: RepoConfig, component: str = "aio") -> str:
    config = component_config(repo, component)
    revision_arg = str(config.get("registry_revision_arg", "") or "").strip()
    if not revision_arg:
        return ""
    upstream_version = _read_component_upstream_version(repo, component)
    if not upstream_version:
        return ""
    revision = _read_component_arg(repo, component, revision_arg)
    if not revision:
        return ""
    release_suffix = str(config.get("release_suffix", "aio"))
    return f"{upstream_version}-{release_suffix}.{revision}"


def _version_tags_allowed(
    repo: RepoConfig, *, component: str, release_package_tag: str
) -> bool:
    config = component_config(repo, component)
    if str(config.get("release_policy", "")).strip() == "registry_only":
        return bool(release_package_tag)
    if repo.publish_profile == "upstream-aio-track":
        return bool(release_package_tag)
    return True


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


def delete_dockerhub_tags(
    *,
    image: str,
    tags: list[str],
    username: str,
    token: str,
    required_substring: str = "",
    dry_run: bool = False,
) -> list[dict[str, str]]:
    parsed = _dockerhub_image_parts(image)
    if parsed is None:
        raise ValueError(f"{image}: unsupported Docker Hub image format")
    namespace, repository = parsed
    quoted_namespace = urllib.parse.quote(namespace, safe="")
    quoted_repository = urllib.parse.quote(repository, safe="")
    cleaned_tags = _clean_tag_list(tags)
    if not cleaned_tags:
        raise ValueError("at least one Docker Hub tag is required")

    required = required_substring.strip()
    if required:
        for tag in cleaned_tags:
            if required not in tag:
                raise ValueError(
                    f"{tag}: refusing to delete tag without required substring "
                    f"{required!r}"
                )

    if dry_run:
        return [{"tag": tag, "state": "would-delete"} for tag in cleaned_tags]

    if not username or not token:
        raise ValueError("DOCKERHUB_USERNAME and DOCKERHUB_TOKEN are required")

    auth_token = _dockerhub_login_token(username=username, token=token)
    results: list[dict[str, str]] = []
    for tag in cleaned_tags:
        quoted_tag = urllib.parse.quote(tag, safe="")
        url = (
            "https://hub.docker.com/v2/"
            f"namespaces/{quoted_namespace}/repositories/{quoted_repository}/tags/{quoted_tag}"
        )
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {auth_token}"},
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:  # nosec B310
                if response.status in {200, 202, 204}:
                    results.append({"tag": tag, "state": "deleted"})
                else:
                    results.append(
                        {"tag": tag, "state": f"unexpected:{response.status}"}
                    )
        except urllib.error.HTTPError as error:
            if error.code == 404:
                results.append({"tag": tag, "state": "missing"})
            elif error.code == 403:
                raise RuntimeError(
                    f"{tag}: Docker Hub delete forbidden for "
                    f"{namespace}/{repository}; the Docker Hub token "
                    "authenticated but lacks tag delete/admin permission"
                ) from error
            else:
                raise RuntimeError(
                    f"{tag}: Docker Hub delete failed: HTTP {error.code}: "
                    f"{error.reason}"
                ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(
                f"{tag}: Docker Hub delete failed: {error.reason}"
            ) from error
    return results


def dockerhub_auth_preflight_failure(*, username: str, token: str) -> str | None:
    if not username or not token:
        return "DOCKERHUB_USERNAME and Docker Hub token are required"
    try:
        _dockerhub_login_token(username=username, token=token)
    except RuntimeError as exc:
        return str(exc)
    return None


def dockerhub_delete_scope_preflight_failure(
    *,
    image: str,
    username: str,
    token: str,
    probe_tag: str | None = None,
) -> str | None:
    parsed = _dockerhub_image_parts(image)
    if parsed is None:
        return f"{image}: unsupported Docker Hub image format"
    if not username or not token:
        return "DOCKERHUB_USERNAME and DOCKERHUB_DELETE_TOKEN are required"

    namespace, repository = parsed
    tag = probe_tag or f"aio-fleet-preflight-missing-{uuid.uuid4().hex}"
    try:
        auth_token = _dockerhub_login_token(username=username, token=token)
    except RuntimeError as exc:
        return str(exc)
    request = urllib.request.Request(
        _dockerhub_tag_delete_url(namespace, repository, tag),
        headers={"Authorization": f"Bearer {auth_token}"},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # nosec B310
            if response.status in {200, 202, 204, 404}:
                return None
            return f"{image}: Docker Hub delete probe returned HTTP {response.status}"
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        if error.code == 403:
            return (
                f"{image}: Docker Hub delete forbidden; "
                "DOCKERHUB_DELETE_TOKEN must have tag delete/admin permission"
            )
        return f"{image}: Docker Hub delete probe failed: HTTP {error.code}: {error.reason}"
    except urllib.error.URLError as error:
        return f"{image}: Docker Hub delete probe failed: {error.reason}"


def _dockerhub_tag_delete_url(namespace: str, repository: str, tag: str) -> str:
    quoted_namespace = urllib.parse.quote(namespace, safe="")
    quoted_repository = urllib.parse.quote(repository, safe="")
    quoted_tag = urllib.parse.quote(tag, safe="")
    return (
        "https://hub.docker.com/v2/"
        f"namespaces/{quoted_namespace}/repositories/{quoted_repository}/tags/{quoted_tag}"
    )


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
    quoted_namespace = urllib.parse.quote(namespace, safe="")
    quoted_repository = urllib.parse.quote(repository, safe="")
    quoted_tag = urllib.parse.quote(tag_name, safe="")
    url = (
        "https://hub.docker.com/v2/repositories/"
        f"{quoted_namespace}/{quoted_repository}/tags/{quoted_tag}"
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
    parsed = _dockerhub_image_parts(image)
    if parsed is None:
        return None
    namespace, repository = parsed
    if not tag_name:
        return None
    return namespace, repository, tag_name


_DOCKERHUB_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


def _dockerhub_image_parts(image: str) -> tuple[str, str] | None:
    parts = image.split("/")
    if parts and parts[0] in {"docker.io", "index.docker.io"}:
        parts = parts[1:]
    if len(parts) == 1:
        namespace, repository = "library", parts[0]
    elif len(parts) == 2:
        namespace, repository = parts
    else:
        return None
    if not namespace or not repository or ":" in repository:
        return None
    if (
        _DOCKERHUB_NAME_PATTERN.fullmatch(namespace) is None
        or _DOCKERHUB_NAME_PATTERN.fullmatch(repository) is None
    ):
        return None
    return namespace, repository


def _clean_tag_list(tags: list[str]) -> list[str]:
    cleaned = [str(tag).strip() for tag in tags if str(tag).strip()]
    return list(dict.fromkeys(cleaned))


def _dockerhub_login_token(*, username: str, token: str) -> str:
    payload = json.dumps({"identifier": username, "secret": token}).encode()
    request = urllib.request.Request(
        "https://hub.docker.com/v2/auth/token",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # nosec B310
            body = json.load(response)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError(
            f"Docker Hub login returned invalid JSON: {error}"
        ) from error
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"Docker Hub login failed: HTTP {error.code}: {error.reason}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Docker Hub login failed: {error.reason}") from error
    auth_token = str(body.get("access_token", "") or body.get("token", "") or "")
    if not auth_token:
        raise RuntimeError("Docker Hub login did not return a token")
    return auth_token


def _component_image_name(repo: RepoConfig, component: str) -> str:
    components = repo.raw.get("components")
    if isinstance(components, dict):
        config = components.get(component)
        if isinstance(config, dict) and config.get("image_name"):
            return str(config["image_name"])
    return repo.image_name


def _component_floating_tags(repo: RepoConfig, component: str) -> list[str]:
    tags = component_config(repo, component).get("floating_tags", ["latest"])
    if isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, list):
        return ["latest"]
    cleaned = [str(tag).strip() for tag in tags if str(tag).strip()]
    return list(dict.fromkeys(cleaned)) or ["latest"]


def _component_sha_tag(repo: RepoConfig, component: str, sha: str) -> str:
    prefix = str(component_config(repo, component).get("sha_tag_prefix", "sha-"))
    return f"{prefix}{sha}"


def _component_bool(repo: RepoConfig, component: str, key: str, default: bool) -> bool:
    value = component_config(repo, component).get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _read_component_upstream_version(repo: RepoConfig, component: str) -> str:
    try:
        config = component_config(repo, component)
        return read_upstream_version(
            repo.path / str(config.get("dockerfile", "Dockerfile")),
            repo.path / str(config.get("upstream_config", "upstream.toml")),
            version_key=str(config.get("upstream_version_key", "UPSTREAM_VERSION")),
        )
    except (Exception, SystemExit):
        return ""


def _read_component_arg(repo: RepoConfig, component: str, arg_name: str) -> str:
    try:
        config = component_config(repo, component)
        dockerfile = repo.path / str(config.get("dockerfile", "Dockerfile"))
        pattern = re.compile(rf"^ARG {re.escape(arg_name)}=(.+)$")
        for line in dockerfile.read_text().splitlines():
            match = pattern.match(line.strip())
            if match:
                return match.group(1).split("@", 1)[0]
    except (Exception, SystemExit):
        return ""
    return ""


def _release_package_tag(repo: RepoConfig, *, sha: str, component: str) -> str:
    config = component_config(repo, component)
    if str(config.get("release_policy", "")).strip() == "registry_only":
        return component_registry_release_tag(repo, component)
    upstream_version = _read_component_upstream_version(repo, component)
    if not upstream_version:
        return ""
    release_suffix = str(config.get("release_suffix", "aio"))
    changelog_path = repo.path / "CHANGELOG.md"
    try:
        if repo.publish_profile == "changelog-version":
            changelog_version = latest_changelog_version(changelog_path)
        else:
            changelog_version = latest_component_changelog_version(
                changelog_path,
                upstream_version=upstream_version,
                suffix=release_suffix,
            )
    except (Exception, SystemExit):
        return ""
    try:
        release_target_commit = find_release_target_commit(repo.path, changelog_version)
    except (Exception, SystemExit):
        release_target_commit = ""
    if not _release_tag_sha_allowed(
        repo,
        release_target_commit,
        sha,
        component=component,
        release_suffix=release_suffix,
    ):
        return ""

    match = re.match(
        rf"^{re.escape(upstream_version)}-{re.escape(release_suffix)}\.(\d+)$",
        changelog_version,
    )
    if not match:
        return changelog_version if repo.publish_profile == "changelog-version" else ""

    revision = match.group(1)
    if repo.publish_profile == "upstream-aio-track":
        return f"{upstream_version}-{release_suffix}.{revision}"
    return changelog_version


_RELEASE_FORMAT_SUBJECT = re.compile(
    r"^chore\(release\): format .+ changelog(?: \(#\d+\))?$"
)


def _release_tag_sha_allowed(
    repo: RepoConfig,
    release_target_commit: str,
    sha: str,
    *,
    component: str = "aio",
    release_suffix: str = "aio",
) -> bool:
    if release_target_commit == sha:
        return True
    try:
        if not git_is_ancestor(repo.path, release_target_commit, sha):
            return False
        subjects = git(
            repo.path, "log", "--format=%s", f"{release_target_commit}..{sha}"
        )
        changed_files = git(
            repo.path, "diff", "--name-only", f"{release_target_commit}..{sha}"
        )
    except (Exception, SystemExit):
        return False

    subject_lines = [
        subject.strip() for subject in subjects.splitlines() if subject.strip()
    ]
    changed_paths = [
        path.strip() for path in changed_files.splitlines() if path.strip()
    ]
    if not subject_lines:
        return False
    if all(_RELEASE_FORMAT_SUBJECT.match(subject) for subject in subject_lines):
        return changed_paths == ["CHANGELOG.md"]
    allowed_paths = _component_release_followup_paths(repo, component)
    return set(changed_paths).issubset(allowed_paths) and all(
        _release_followup_subject_allowed(
            repo, subject, component=component, release_suffix=release_suffix
        )
        for subject in subject_lines
    )


def _component_release_followup_paths(repo: RepoConfig, component: str) -> set[str]:
    paths = {"CHANGELOG.md"}
    components = repo.raw.get("components")
    if not isinstance(components, dict):
        return paths
    for name, config in components.items():
        if name == component or not isinstance(config, dict):
            continue
        xml_paths = config.get("xml_paths", [])
        if isinstance(xml_paths, str):
            candidate_paths = [xml_paths]
        elif isinstance(xml_paths, list):
            candidate_paths = [str(path) for path in xml_paths]
        else:
            candidate_paths = []
        paths.update(path for path in candidate_paths if path.endswith(".xml"))
    return paths


def _release_followup_subject_allowed(
    repo: RepoConfig, subject: str, *, component: str, release_suffix: str
) -> bool:
    if _RELEASE_FORMAT_SUBJECT.match(subject):
        return True
    components = repo.raw.get("components")
    if not isinstance(components, dict):
        return False
    for name, config in components.items():
        if name == component or not isinstance(config, dict):
            continue
        other_suffix = str(config.get("release_suffix", "aio"))
        if other_suffix == release_suffix:
            continue
        pattern = re.compile(
            rf"^chore\(release\): .+-{re.escape(other_suffix)}\.\d+" r"(?: \(#\d+\))?$"
        )
        if pattern.match(subject):
            return True
    return False
