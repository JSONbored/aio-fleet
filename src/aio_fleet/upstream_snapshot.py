"""Fetch release metadata once, before running any application generators."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from aio_fleet.manifest import FleetManifest, load_manifest
from aio_fleet.upstream import http_json, monitor_configs


def _github_page(repository: str, resource: str, page: int = 1) -> list[dict[str, Any]]:
    url = f"https://api.github.com/repos/{repository}/{resource}?per_page=100&page={page}"
    rows = http_json(url)
    if not isinstance(rows, list):
        raise ValueError(f"unexpected GitHub {resource} response for {repository}")
    return [row for row in rows if isinstance(row, dict)]


def collect_metadata(manifest: FleetManifest) -> dict[str, dict[str, Any]]:
    required: dict[str, set[str]] = {}
    for repo in manifest.repos.values():
        for config in monitor_configs(repo):
            source = config.get("source")
            if source not in {"github-releases", "github-tags"}:
                continue
            resources = required.setdefault(str(config["repo"]), set())
            resources.add("releases" if source == "github-releases" else "tags")
            if config.get("commit_key"):
                resources.add("commits")

    snapshot: dict[str, dict[str, Any]] = {}
    for repository, resources in sorted(required.items()):
        entry: dict[str, Any] = {}
        if "releases" in resources:
            entry["releases"] = [
                {"tag_name": row["tag_name"], "prerelease": bool(row.get("prerelease"))}
                for row in _github_page(repository, "releases")
                if isinstance(row.get("tag_name"), str)
            ]
        if resources.intersection({"tags", "commits"}):
            tags = _github_page(repository, "tags")
            if "tags" in resources:
                entry["tags"] = [{"name": tag["name"]} for tag in tags]
            if "commits" in resources:
                # A release can point to a tag older than the first page of tags.
                needed = {row["tag_name"] for row in entry.get("releases", [])}
                needed.update(row["name"] for row in entry.get("tags", []))
                commits: dict[str, str] = {}
                page = 1
                while tags:
                    for tag in tags:
                        if tag["name"] in needed:
                            commits[tag["name"]] = tag["commit"]["sha"]
                    if needed.issubset(commits) or len(tags) < 100:
                        break
                    page += 1
                    tags = _github_page(repository, "tags", page)
                entry["commits"] = commits
        snapshot[repository] = entry
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = collect_metadata(load_manifest(args.manifest))
    args.output.write_text(json.dumps(snapshot), encoding="utf-8")


if __name__ == "__main__":
    main()
