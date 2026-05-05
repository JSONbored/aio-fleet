from __future__ import annotations

from pathlib import Path

from aio_fleet import poll
from aio_fleet.manifest import load_manifest


def test_poll_targets_skip_cross_repository_pull_requests(
    tmp_path: Path, monkeypatch
) -> None:
    manifest_path = _write_manifest(tmp_path)
    manifest = load_manifest(manifest_path)

    monkeypatch.setattr(
        poll,
        "_open_pull_requests",
        lambda _repo: [
            {
                "number": 1,
                "headRefOid": "a" * 40,
                "isCrossRepository": True,
            },
            {
                "number": 2,
                "headRefOid": "b" * 40,
                "isCrossRepository": False,
            },
        ],
    )
    monkeypatch.setattr(poll, "_main_sha", lambda _repo: "")

    targets = poll.poll_targets(manifest)

    assert [(target.sha, target.source) for target in targets] == [  # nosec B101
        ("b" * 40, "pr:2")
    ]


def test_poll_targets_require_same_repository_pr_identity(
    tmp_path: Path, monkeypatch
) -> None:
    manifest_path = _write_manifest(tmp_path)
    manifest = load_manifest(manifest_path)

    monkeypatch.setattr(
        poll,
        "_open_pull_requests",
        lambda _repo: [
            {
                "number": 1,
                "headRefOid": "a" * 40,
                "headRepository": {"nameWithOwner": "someone-else/example-aio"},
            },
            {
                "number": 2,
                "headRefOid": "b" * 40,
                "headRepository": {"nameWithOwner": "JSONbored/example-aio"},
            },
        ],
    )
    monkeypatch.setattr(poll, "_main_sha", lambda _repo: "")

    targets = poll.poll_targets(manifest)

    assert [(target.sha, target.source) for target in targets] == [  # nosec B101
        ("b" * 40, "pr:2")
    ]


def _write_manifest(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    manifest = tmp_path / "fleet.yml"
    manifest.write_text(f"""
owner: JSONbored
repos:
  example-aio:
    path: {repo_path}
    app_slug: example-aio
    image_name: jsonbored/example-aio
    docker_cache_scope: example-aio-image
    pytest_image_tag: example-aio:pytest
""")
    return manifest
