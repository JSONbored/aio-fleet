from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from aio_fleet.manifest import RepoConfig

RETIRED_SHARED_PATHS: dict[str, str] = {
    ".github/workflows": "app workflows are replaced by aio-fleet check orchestration",
    ".trunk": "Trunk config runs from aio-fleet scratch checkouts",
    "cliff.toml": "git-cliff config is generated centrally",
    "renovate.json": "shared dependency policy moves to aio-fleet",
    "upstream.toml": "upstream provider state moves to .aio-fleet.yml",
    "components.toml": "component metadata moves to .aio-fleet.yml",
    "scripts/release.py": "release helpers are centralized",
    "scripts/update-template-changes.py": "XML Changes rendering is centralized",
    "scripts/check-upstream.py": "upstream monitoring is centralized",
    "scripts/validate-derived-repo.sh": "derived repo validation is centralized",
}


@dataclass(frozen=True)
class CleanupFinding:
    path: Path
    reason: str


def cleanup_findings(repo: RepoConfig) -> list[CleanupFinding]:
    findings: list[CleanupFinding] = []
    for relative, reason in RETIRED_SHARED_PATHS.items():
        path = repo.path / relative
        if path.exists():
            findings.append(CleanupFinding(path=path, reason=reason))
    return findings


def remove_cleanup_findings(findings: list[CleanupFinding]) -> None:
    for finding in findings:
        if finding.path.is_dir():
            shutil.rmtree(finding.path)
        else:
            finding.path.unlink()
