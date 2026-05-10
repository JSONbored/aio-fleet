from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


def test_poll_targets_emit_checkout_submodules_for_same_repo_pr_and_main(
    tmp_path: Path, monkeypatch
) -> None:
    manifest_path = _write_manifest(tmp_path, checkout_submodules=True)
    manifest = load_manifest(manifest_path)

    monkeypatch.setattr(
        poll,
        "_open_pull_requests",
        lambda _repo: [
            {
                "number": 2,
                "headRefOid": "b" * 40,
                "isCrossRepository": False,
            },
        ],
    )
    monkeypatch.setattr(poll, "_main_sha", lambda _repo: "c" * 40)

    targets = poll.poll_targets(manifest)

    assert [target.event for target in targets] == [  # nosec B101
        "pull_request",
        "push",
    ]
    assert targets[0].checkout_submodules is True  # nosec B101
    assert targets[1].checkout_submodules is True  # nosec B101


def test_publish_required_ignores_docs_only_main_commits(
    tmp_path: Path, monkeypatch
) -> None:
    manifest_path = _write_manifest(tmp_path)
    repo = load_manifest(manifest_path).repo("example-aio")

    monkeypatch.setattr(
        poll, "_commit_changed_paths", lambda _repo, _sha: ["docs/a.md"]
    )

    assert (
        poll.publish_required(repo, sha="a" * 40, event="push") is False
    )  # nosec B101


def test_publish_required_accepts_runtime_and_release_commits(
    tmp_path: Path, monkeypatch
) -> None:
    manifest_path = _write_manifest(tmp_path)
    repo = load_manifest(manifest_path).repo("example-aio")

    for path in ("Dockerfile", "rootfs/etc/services.d/web/run", "CHANGELOG.md"):
        monkeypatch.setattr(
            poll,
            "_commit_changed_paths",
            lambda _repo, _sha, changed_path=path: [changed_path],
        )

        assert (
            poll.publish_required(repo, sha="a" * 40, event="push") is True
        )  # nosec B101


def test_poll_targets_publish_only_for_publish_related_main_commits(
    tmp_path: Path, monkeypatch
) -> None:
    manifest_path = _write_manifest(tmp_path)
    manifest = load_manifest(manifest_path)

    monkeypatch.setattr(poll, "_open_pull_requests", lambda _repo: [])
    monkeypatch.setattr(poll, "_main_sha", lambda _repo: "c" * 40)
    monkeypatch.setattr(
        poll, "_commit_changed_paths", lambda _repo, _sha: ["README.md"]
    )

    targets = poll.poll_targets(manifest)

    assert len(targets) == 1  # nosec B101
    assert targets[0].publish is False  # nosec B101

    monkeypatch.setattr(
        poll, "_commit_changed_paths", lambda _repo, _sha: ["Dockerfile"]
    )

    targets = poll.poll_targets(manifest)

    assert len(targets) == 1  # nosec B101
    assert targets[0].publish is True  # nosec B101


def test_poll_gh_maps_app_token_to_gh_token(monkeypatch) -> None:
    captured_env: dict[str, str] = {}

    def fake_run(_command: list[str], **kwargs):
        nonlocal captured_env
        captured_env = dict(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setenv("APP_TOKEN", "app-token")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(poll.shutil, "which", lambda _name: "gh")
    monkeypatch.setattr(poll.subprocess, "run", fake_run)

    result = poll._gh(["pr", "list"])

    assert result.returncode == 0  # nosec B101
    assert captured_env["GH_TOKEN"] == "app-token"  # nosec B101
    assert "GITHUB_TOKEN" not in captured_env  # nosec B101


def _write_manifest(tmp_path: Path, *, checkout_submodules: bool = False) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    checkout_line = "    checkout_submodules: true\n" if checkout_submodules else ""
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
{checkout_line.rstrip()}
""")
    return manifest
