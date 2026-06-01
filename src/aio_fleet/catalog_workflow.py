from __future__ import annotations

import subprocess  # nosec B404
from pathlib import Path

VALIDATE_WORKFLOW = ".github/workflows/validate-catalog.yml"
RETIRED_CATALOG_PATHS = {
    ".github/workflows/changelog.yml": "catalog changelog PRs are created by aio-fleet",
    "scripts/validate-readme-inventory.py": "catalog README inventory validation runs in aio-fleet",
    "cliff.toml": "catalog changelog rendering is centralized in aio-fleet",
    ".trunk": "Trunk config runs from aio-fleet scratch checkouts",
}


def current_aio_fleet_ref(aio_fleet_root: Path) -> str:
    result = subprocess.run(  # nosec B603
        ["git", "rev-parse", "HEAD"],
        cwd=aio_fleet_root,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "unable to resolve aio-fleet HEAD")
    return result.stdout.strip()


def render_validate_catalog_workflow(aio_fleet_ref: str) -> str:
    return f"""name: Validate Catalog

on:
  pull_request:
  push:
    branches:
      - main

permissions:
  contents: read

jobs:
  validate-catalog:
    name: validate-catalog
    runs-on: ubuntu-latest
    steps:
      - name: Checkout catalog
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2

      - name: Checkout aio-fleet
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          repository: JSONbored/aio-fleet
          ref: {aio_fleet_ref}
          path: .aio-fleet

      - name: Install aio-fleet
        run: python3 -m pip install ./.aio-fleet

      - name: Validate catalog
        run: python3 -m aio_fleet --manifest .aio-fleet/fleet.yml validate-catalog --catalog-path .

      - name: Audit catalog quality
        run: python3 -m aio_fleet --manifest .aio-fleet/fleet.yml catalog-audit --catalog-path .
"""


def catalog_workflow_findings(catalog_path: Path, *, aio_fleet_ref: str) -> list[str]:
    findings: list[str] = []
    workflow = catalog_path / VALIDATE_WORKFLOW
    expected = render_validate_catalog_workflow(aio_fleet_ref)
    if not workflow.exists():
        findings.append(f"{VALIDATE_WORKFLOW}: missing central catalog workflow")
    elif workflow.read_text() != expected:
        findings.append(f"{VALIDATE_WORKFLOW}: drifted from aio-fleet renderer")

    for relative, reason in RETIRED_CATALOG_PATHS.items():
        if (catalog_path / relative).exists():
            findings.append(f"{relative}: {reason}")
    return findings


def write_validate_catalog_workflow(catalog_path: Path, *, aio_fleet_ref: str) -> Path:
    workflow = catalog_path / VALIDATE_WORKFLOW
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(render_validate_catalog_workflow(aio_fleet_ref))
    return workflow
