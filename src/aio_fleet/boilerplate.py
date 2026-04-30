from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from aio_fleet.manifest import RepoConfig


@dataclass(frozen=True)
class BoilerplateChange:
    repo: str
    target: Path
    action: str


def sync_boilerplate(
    repo: RepoConfig,
    *,
    config_path: Path,
    profile: str,
    dry_run: bool,
) -> list[BoilerplateChange]:
    config = _load_config(config_path)
    profiles = config.get("profiles", {})
    if profile not in profiles:
        raise ValueError(f"unknown boilerplate profile: {profile}")

    changes: list[BoilerplateChange] = []
    root = config_path.parent
    files = profiles[profile].get("files", [])
    for item in files:
        if not _applies_to_repo(repo, item):
            continue
        source = root / str(item["source"])
        target = repo.path / str(item["target"])
        if bool(item.get("if_missing", False)) and target.exists():
            continue
        desired = source.read_text()
        if bool(item.get("template", False)):
            desired = _render_template(desired, repo)
        current = target.read_text() if target.exists() else None
        if current == desired:
            continue

        action = "create" if current is None else "update"
        changes.append(BoilerplateChange(repo=repo.name, target=target, action=action))
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(desired)

    return changes


def _load_config(config_path: Path) -> dict[str, Any]:
    data = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{config_path} must contain a mapping")
    return data


def _applies_to_repo(repo: RepoConfig, item: dict[str, Any]) -> bool:
    only = _string_set(item.get("only_repos"))
    if only and repo.name not in only:
        return False

    excluded = _string_set(item.get("exclude_repos"))
    return repo.name not in excluded


def _string_set(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {str(item) for item in value}
    raise ValueError("boilerplate repo filters must be strings or lists")


def _render_template(text: str, repo: RepoConfig) -> str:
    values = {
        "app_slug": repo.app_slug,
        "image_name": repo.image_name,
        "owner": repo.owner,
        "repo": repo.name,
        "github_repo": repo.github_repo,
        "release_name": str(repo.raw.get("release_name", repo.app_slug)),
        "upstream_name": str(repo.raw.get("upstream_name", repo.app_slug)),
    }
    rendered = text
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{ {key} }}}}", value)
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered
