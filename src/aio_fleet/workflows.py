from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from aio_fleet.manifest import FleetManifest, RepoConfig


def _yaml_list(values: Iterable[str], indent: int = 6) -> str:
    prefix = " " * indent
    return "\n".join(f'{prefix}- "{_quote(value)}"' for value in values)


def _block(values: Iterable[str], indent: int = 8) -> str:
    prefix = " " * indent
    return "\n".join(f"{prefix}{value}" for value in values)


def _quote(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _catalog_block(repo: RepoConfig) -> str:
    assets = repo.raw.get("catalog_assets", [])
    lines = [f"{item['source']}|{item['target']}" for item in assets]
    return _block(lines)


def _xml_block(repo: RepoConfig) -> str:
    return _block(repo.list_value("xml_paths"))


def _extra_publish_block(repo: RepoConfig) -> str:
    return _block(repo.list_value("extra_publish_paths"))


def _workflow_paths(repo: RepoConfig) -> list[str]:
    paths = [
        "CHANGELOG.md",
        "Dockerfile",
        "cliff.toml",
        "pyproject.toml",
        "requirements-dev.txt",
        "docs/upstream/**",
        "rootfs/**",
        "scripts/**",
        "tests/**",
        ".trunk/**",
        "upstream.toml",
        "components.toml",
        "components/**",
        "assets/**",
        "renovate.json",
        ".github/workflows/**",
    ]
    paths.extend(repo.list_value("xml_paths"))
    paths.extend(repo.list_value("extra_publish_paths"))
    return sorted(dict.fromkeys(paths))


def _bool_literal(value: object) -> str:
    return "true" if bool(value) else "false"


def _empty_safe(value: object) -> str:
    text = "" if value is None else str(value)
    return '""' if text == "" else text


def _uses(manifest: FleetManifest, workflow_path: str, reusable_ref: str) -> str:
    return f"{manifest.owner}/aio-fleet/{workflow_path}@{reusable_ref}"


def render_caller_workflow(
    manifest: FleetManifest,
    repo: RepoConfig,
    reusable_ref: str,
) -> str:
    workflow_path = manifest.reusable_workflow.get("path", ".github/workflows/aio-build.yml")
    uses = f"{manifest.owner}/aio-fleet/{workflow_path}@{reusable_ref}"
    paths = _yaml_list(_workflow_paths(repo), indent=6)
    extra_inputs = ""
    extended = repo.extended_integration
    dispatch = "  workflow_dispatch:"
    run_extended = "false"
    extended_pytest_args = ""
    if extended:
        input_name = str(extended.get("input_name", "run_extended_integration"))
        description = str(extended.get("description", "Run extended integration tests"))
        dispatch = f"""  workflow_dispatch:
    inputs:
      {input_name}:
        description: {description}
        required: false
        default: false
        type: boolean"""
        run_extended = f"${{{{ github.event_name == 'workflow_dispatch' && inputs.{input_name} }}}}"
        extended_pytest_args = str(extended.get("pytest_args", ""))

    if repo.is_signoz_suite:
        components = repo.raw["components"]
        agent = components["agent"]
        extra_inputs = f"""
      agent_image_name: {agent['image_name']}
      agent_docker_cache_scope: {agent['docker_cache_scope']}
      agent_pytest_image_tag: {agent['pytest_image_tag']}
      agent_integration_pytest_args: {agent['integration_pytest_args']}
      agent_context: {agent['context']}
      agent_dockerfile: {agent['dockerfile']}
      agent_upstream_name: {agent['upstream_name']}
      agent_image_description: {agent['image_description']}"""

    return f"""name: {repo.workflow_name}

on:
  push:
    branches: [main]
    paths:
{paths}
  pull_request:
    branches: [main]
    paths:
{paths}
{dispatch}

permissions:
  contents: read

concurrency:
  group: ${{{{ github.workflow }}}}-${{{{ github.ref }}}}
  cancel-in-progress: true

jobs:
  aio-build:
    uses: {uses}
    permissions:
      contents: read
      packages: write
      pull-requests: write
    with:
      app_slug: {repo.app_slug}
      image_name: {repo.image_name}
      workflow_title: {repo.workflow_name}
      docker_cache_scope: {repo.get('docker_cache_scope')}
      pytest_image_tag: {repo.get('pytest_image_tag')}
      publish_profile: {repo.publish_profile}
      upstream_name: {repo.get('upstream_name')}
      image_description: {repo.get('image_description')}
      python_version: "{repo.get('python_version')}"
      trunk_org_slug: {repo.get('trunk_org_slug')}
      publish_platforms: {repo.get('publish_platforms')}
      checkout_submodules: {_bool_literal(repo.get('checkout_submodules', False))}
      integration_pytest_args: {repo.get('integration_pytest_args')}
      run_extended_integration: {run_extended}
      extended_integration_pytest_args: {_empty_safe(extended_pytest_args)}
      generator_check_command: {_empty_safe(repo.get('generator_check_command', ''))}
      upstream_digest_arg: {repo.get('upstream_digest_arg', 'UPSTREAM_IMAGE_DIGEST')}
      xml_paths: |
{_xml_block(repo)}
      extra_publish_paths: |
{_extra_publish_block(repo)}
      catalog_assets: |
{_catalog_block(repo)}{extra_inputs}
    secrets: inherit
"""


def render_check_upstream_workflow(
    manifest: FleetManifest,
    repo: RepoConfig,
    reusable_ref: str,
) -> str:
    uses = _uses(manifest, ".github/workflows/aio-check-upstream.yml", reusable_ref)
    components = repo.list_value("upstream_components") or [""]
    commit_paths = repo.list_value("upstream_commit_paths") or ["Dockerfile"]
    return f"""name: {repo.get('check_upstream_name', 'Check Upstream Version')}

on:
  schedule:
    - cron: "23 7 * * 1"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{{{ github.workflow }}}}-${{{{ github.ref }}}}
  cancel-in-progress: true

jobs:
  check-upstream:
    uses: {uses}
    permissions:
      contents: write
      pull-requests: write
      issues: write
    with:
      workflow_title: {repo.get('check_upstream_name', 'Check Upstream Version')}
      component_matrix: '{json.dumps(components)}'
      commit_paths: |
{_block(commit_paths)}
    secrets: inherit
"""


def _release_name(repo: RepoConfig, component: str = "") -> str:
    if component and repo.is_signoz_suite:
        if component == "signoz-agent":
            return str(repo.raw["components"]["agent"].get("release_name", "SigNoz Agent"))
        return str(repo.raw.get("release_name", "SigNoz-AIO"))
    return str(repo.raw.get("release_name", repo.get("upstream_name", repo.app_slug)))


def render_prepare_release_workflow(
    manifest: FleetManifest,
    repo: RepoConfig,
    reusable_ref: str,
    *,
    component: str = "",
) -> str:
    release_component = "signoz-aio" if repo.is_signoz_suite and not component else component
    release_name = _release_name(repo, release_component)
    uses = _uses(manifest, ".github/workflows/aio-prepare-release.yml", reusable_ref)
    previous_tag_command = repo.get(
        "previous_tag_command",
        "latest-release-tag" if repo.publish_profile == "template" else "latest-aio-tag",
    )
    return f"""name: Prepare Release / {release_name}

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  prepare-release:
    uses: {uses}
    permissions:
      contents: write
      pull-requests: write
    with:
      release_name: {release_name}
      component: {_empty_safe(release_component)}
      component_label: {_empty_safe(release_component)}
      previous_tag_command: {previous_tag_command}
    secrets: inherit
"""


def render_publish_release_workflow(
    manifest: FleetManifest,
    repo: RepoConfig,
    reusable_ref: str,
    *,
    component: str = "",
) -> str:
    release_component = "signoz-aio" if repo.is_signoz_suite and not component else component
    release_name = _release_name(repo, release_component)
    uses = _uses(manifest, ".github/workflows/aio-publish-release.yml", reusable_ref)
    return f"""name: Publish Release / {release_name}

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  publish-release:
    uses: {uses}
    permissions:
      actions: read
      contents: write
    with:
      release_name: {release_name}
      component: {_empty_safe(release_component)}
      workflow_selector: build.yml
    secrets: inherit
"""


def workflow_path_for(repo: RepoConfig) -> Path:
    return repo.path / ".github" / "workflows" / "build.yml"


def check_upstream_workflow_path_for(repo: RepoConfig) -> Path:
    return repo.path / ".github" / "workflows" / "check-upstream.yml"


def prepare_release_workflow_path_for(repo: RepoConfig, *, component: str = "") -> Path:
    if repo.is_signoz_suite and component == "signoz-agent":
        filename = "release-agent.yml"
    else:
        filename = "release.yml"
    return repo.path / ".github" / "workflows" / filename


def publish_release_workflow_path_for(repo: RepoConfig, *, component: str = "") -> Path:
    if repo.is_signoz_suite and component == "signoz-agent":
        filename = "publish-release-agent.yml"
    else:
        filename = "publish-release.yml"
    return repo.path / ".github" / "workflows" / filename


def rendered_workflows(
    manifest: FleetManifest,
    repo: RepoConfig,
    reusable_ref: str,
) -> dict[Path, str]:
    workflows = {
        workflow_path_for(repo): render_caller_workflow(manifest, repo, reusable_ref),
        check_upstream_workflow_path_for(repo): render_check_upstream_workflow(
            manifest, repo, reusable_ref
        ),
        prepare_release_workflow_path_for(repo): render_prepare_release_workflow(
            manifest, repo, reusable_ref
        ),
        publish_release_workflow_path_for(repo): render_publish_release_workflow(
            manifest, repo, reusable_ref
        ),
    }
    if repo.is_signoz_suite:
        workflows[
            prepare_release_workflow_path_for(repo, component="signoz-agent")
        ] = render_prepare_release_workflow(
            manifest,
            repo,
            reusable_ref,
            component="signoz-agent",
        )
        workflows[
            publish_release_workflow_path_for(repo, component="signoz-agent")
        ] = render_publish_release_workflow(
            manifest,
            repo,
            reusable_ref,
            component="signoz-agent",
        )
    return workflows
