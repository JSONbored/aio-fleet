from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

FLEET_REPORT_SCHEMA_VERSION = 3
FLEET_REPORT_TOP_LEVEL_KEYS = (
    "schema_version",
    "generated_at",
    "issue_repo",
    "warnings",
    "summary",
    "rows",
    "activity",
    "destination_repos",
    "rehab_repos",
    "registry",
    "releases",
    "cleanup",
    "workflow",
)


@dataclass(frozen=True)
class FleetReport:
    """Versioned report envelope shared by dashboard, alerts, and future UIs."""

    generated_at: str
    issue_repo: str
    summary: dict[str, Any]
    rows: list[dict[str, Any]]
    activity: list[dict[str, Any]] = field(default_factory=list)
    destination_repos: list[dict[str, Any]] = field(default_factory=list)
    rehab_repos: list[dict[str, Any]] = field(default_factory=list)
    registry: list[dict[str, Any]] = field(default_factory=list)
    releases: list[dict[str, Any]] = field(default_factory=list)
    cleanup: list[dict[str, Any]] = field(default_factory=list)
    workflow: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    schema_version: int = FLEET_REPORT_SCHEMA_VERSION

    def to_state(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "issue_repo": self.issue_repo,
            "warnings": self.warnings,
            "summary": self.summary,
            "rows": self.rows,
            "activity": self.activity,
            "destination_repos": self.destination_repos,
            "rehab_repos": self.rehab_repos,
            "registry": self.registry,
            "releases": self.releases,
            "cleanup": self.cleanup,
            "workflow": self.workflow,
        }


def stable_report_json(state: dict[str, Any]) -> str:
    """Render report JSON in a deterministic form for snapshots and consumers."""

    return json.dumps(state, indent=2, sort_keys=True)


def fleet_report_json_schema() -> dict[str, Any]:
    """Return the stable top-level schema consumed by future UI surfaces."""

    object_map = {"type": "object", "additionalProperties": True}
    array_map = {"type": "array", "items": object_map}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/JSONbored/aio-fleet/schemas/fleet-report-v3.json",
        "title": "AIO Fleet Report",
        "type": "object",
        "additionalProperties": False,
        "required": list(FLEET_REPORT_TOP_LEVEL_KEYS),
        "properties": {
            "schema_version": {"const": FLEET_REPORT_SCHEMA_VERSION},
            "generated_at": {"type": "string"},
            "issue_repo": {"type": "string"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "summary": object_map,
            "rows": array_map,
            "activity": array_map,
            "destination_repos": array_map,
            "rehab_repos": array_map,
            "registry": array_map,
            "releases": array_map,
            "cleanup": array_map,
            "workflow": object_map,
        },
    }


def validate_report_shape(state: dict[str, Any]) -> list[str]:
    """Validate the versioned report envelope without pulling in jsonschema."""

    failures: list[str] = []
    for key in FLEET_REPORT_TOP_LEVEL_KEYS:
        if key not in state:
            failures.append(f"missing top-level key: {key}")
    extra = sorted(set(state) - set(FLEET_REPORT_TOP_LEVEL_KEYS))
    for key in extra:
        failures.append(f"unexpected top-level key: {key}")
    if state.get("schema_version") != FLEET_REPORT_SCHEMA_VERSION:
        failures.append(
            f"unsupported schema_version: {state.get('schema_version')!r}; expected {FLEET_REPORT_SCHEMA_VERSION}"
        )
    for key in (
        "warnings",
        "rows",
        "activity",
        "destination_repos",
        "rehab_repos",
        "registry",
        "releases",
        "cleanup",
    ):
        if key in state and not isinstance(state[key], list):
            failures.append(f"{key}: expected list")
    for key in ("summary", "workflow"):
        if key in state and not isinstance(state[key], dict):
            failures.append(f"{key}: expected object")
    for key in ("generated_at", "issue_repo"):
        if key in state and not isinstance(state[key], str):
            failures.append(f"{key}: expected string")
    return failures
