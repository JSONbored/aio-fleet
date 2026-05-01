from __future__ import annotations

from pathlib import Path

from aio_fleet.cleanup import cleanup_findings
from aio_fleet.manifest import load_manifest


def test_cleanup_findings_detect_retired_shared_files(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    (repo_path / ".github" / "workflows").mkdir(parents=True)
    (repo_path / ".trunk").mkdir()
    (repo_path / "scripts").mkdir()
    (repo_path / "scripts" / "release.py").write_text("")
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
    repo = load_manifest(manifest).repo("example-aio")

    findings = cleanup_findings(repo)

    assert [
        finding.path.relative_to(repo_path).as_posix() for finding in findings
    ] == [  # nosec B101
        ".github/workflows",
        ".trunk",
        "scripts/release.py",
    ]
