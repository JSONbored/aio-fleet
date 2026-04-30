from __future__ import annotations

from pathlib import Path
from typing import Iterable

from aio_fleet.manifest import FleetManifest, RepoConfig


def _yaml_list(values: Iterable[str], indent: int = 6) -> str:
    prefix = " " * indent
    return "\n".join(f'{prefix}- "{_quote(value)}"' for value in values)


def _block(values: Iterable[str], indent: int = 10) -> str:
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
        ".github/actions/**",
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
      extended_integration_pytest_args: {extended_pytest_args}
      generator_check_command: {repo.get('generator_check_command', '')}
      upstream_digest_arg: {repo.get('upstream_digest_arg', 'UPSTREAM_IMAGE_DIGEST')}
      xml_paths: |
{_xml_block(repo)}
      extra_publish_paths: |
{_extra_publish_block(repo)}
      catalog_assets: |
{_catalog_block(repo)}{extra_inputs}
    secrets: inherit
"""


def workflow_path_for(repo: RepoConfig) -> Path:
    return repo.path / ".github" / "workflows" / "build.yml"
