from __future__ import annotations

import json

import pytest

from aio_fleet import upstream, upstream_snapshot
from aio_fleet.manifest import FleetManifest


def test_shared_release_snapshot_preserves_channels_and_commit_pins(
    tmp_path, monkeypatch
) -> None:
    monitors = [
        {"source": "github-releases", "repo": "org/app", "commit_key": "COMMIT"},
        {"source": "github-releases", "repo": "org/app", "prerelease_channel": "alpha"},
        {"source": "github-tags", "repo": "org/app"},
    ]
    manifest = FleetManifest(
        tmp_path / "fleet.yml",
        {"owner": "owner", "repos": {"wrapper": {"upstream_monitor": monitors}}},
    )
    calls = []

    def fetch(repository, resource, page=1):
        calls.append((repository, resource, page))
        if resource == "releases":
            return [
                {"tag_name": "v1.0.1", "prerelease": False, "body": "release notes"},
                {"tag_name": "v1.1.0-alpha.1", "prerelease": True},
            ]
        if page == 1:
            return [
                {"name": f"v0.0.{n}", "commit": {"sha": f"{n:040x}"}}
                for n in range(100)
            ]
        return [
            {"name": "v1.0.1", "commit": {"sha": "a" * 40}},
            {"name": "v1.1.0-alpha.1", "commit": {"sha": "b" * 40}},
        ]

    monkeypatch.setattr(upstream_snapshot, "_github_page", fetch)
    data = upstream_snapshot.collect_metadata(manifest)
    assert calls == [  # nosec B101
        ("org/app", "releases", 1),
        ("org/app", "tags", 1),
        ("org/app", "tags", 2),
    ]
    assert "body" not in data["org/app"]["releases"][0]  # nosec B101
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("AIO_FLEET_GITHUB_METADATA", str(path))
    monkeypatch.setattr(upstream, "http_json", lambda *_: pytest.fail("live API read"))
    assert upstream.latest_github_release("org/app", stable_only=True) == "v1.0.1"  # nosec B101
    alpha = upstream.latest_github_release(
        "org/app", stable_only=False, prerelease_channel="alpha"
    )
    assert alpha == "v1.1.0-alpha.1"  # nosec B101
    assert upstream.latest_github_tag("org/app", stable_only=True) == "v0.0.99"  # nosec B101
    assert upstream.resolve_github_commit("org/app", "v1.0.1") == "a" * 40  # nosec B101
    with pytest.raises(ValueError, match="no commit recorded"):
        upstream.resolve_github_commit("org/app", "v2.0.0")
