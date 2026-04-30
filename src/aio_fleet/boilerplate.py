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
        source = root / str(item["source"])
        target = repo.path / str(item["target"])
        desired = source.read_text()
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
