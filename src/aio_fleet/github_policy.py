from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import yaml


def load_policy(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    if "repositories" not in data or not isinstance(data["repositories"], dict):
        raise ValueError(f"{path} must define repositories")
    return data


def validate_github_policy(
    policy_path: Path,
    *,
    repos: list[str] | None = None,
    check_secrets: bool,
) -> list[str]:
    policy = load_policy(policy_path)
    owner = str(policy.get("owner", "JSONbored"))
    defaults = policy.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError("github policy defaults must be a mapping")

    configured = policy["repositories"]
    selected = repos or sorted(configured)
    failures: list[str] = []
    for repo_name in selected:
        if repo_name not in configured:
            failures.append(f"{repo_name}: missing from github policy")
            continue
        repo_policy = _deep_merge(defaults, configured[repo_name])
        failures.extend(_repository_failures(owner, repo_name, repo_policy))
        failures.extend(_branch_protection_failures(owner, repo_name, repo_policy))
        failures.extend(_action_permission_failures(owner, repo_name, repo_policy))
        if check_secrets:
            failures.extend(_secret_failures(owner, repo_name, repo_policy))
    return failures


def _repository_failures(owner: str, repo_name: str, policy: dict[str, Any]) -> list[str]:
    expected = dict(policy.get("repository", {}))
    if not expected:
        return []
    data = _gh_json(["api", f"repos/{owner}/{repo_name}"])
    failures: list[str] = []
    comparisons = {
        "visibility": data.get("visibility"),
        "homepage_url": data.get("homepage"),
        "has_issues": data.get("has_issues"),
        "has_projects": data.get("has_projects"),
        "has_wiki": data.get("has_wiki"),
        "delete_branch_on_merge": data.get("delete_branch_on_merge"),
        "allow_auto_merge": data.get("allow_auto_merge"),
    }
    for key, actual in comparisons.items():
        if key in expected and actual != expected[key]:
            failures.append(f"{repo_name}: repository {key} expected {expected[key]!r}, got {actual!r}")
    return failures


def _branch_protection_failures(owner: str, repo_name: str, policy: dict[str, Any]) -> list[str]:
    expected = dict(policy.get("branch_protection", {}))
    if not expected:
        return []
    branch = str(policy.get("branch", policy.get("default_branch", "main")))
    data = _gh_json(["api", f"repos/{owner}/{repo_name}/branches/{branch}/protection"])

    expected_checks = list(policy.get("required_checks", []))
    actual_checks = data.get("required_status_checks", {}).get("contexts", [])
    failures: list[str] = []
    if set(actual_checks) != set(expected_checks):
        failures.append(
            f"{repo_name}: required checks drift: expected {sorted(expected_checks)}, got {sorted(actual_checks)}"
        )

    strict = data.get("required_status_checks", {}).get("strict")
    if "strict_required_status_checks" in expected and strict != expected["strict_required_status_checks"]:
        failures.append(f"{repo_name}: required status strict expected {expected['strict_required_status_checks']}, got {strict}")

    checks = {
        "enforce_admins": data.get("enforce_admins", {}).get("enabled"),
        "require_conversation_resolution": data.get("required_conversation_resolution", {}).get("enabled"),
        "require_signed_commits": data.get("required_signatures", {}).get("enabled"),
        "required_approving_review_count": data.get("required_pull_request_reviews", {}).get(
            "required_approving_review_count"
        ),
    }
    for key, actual in checks.items():
        if key in expected and actual != expected[key]:
            failures.append(f"{repo_name}: branch protection {key} expected {expected[key]!r}, got {actual!r}")
    return failures


def _action_permission_failures(owner: str, repo_name: str, policy: dict[str, Any]) -> list[str]:
    expected = dict(policy.get("actions", {}))
    if not expected:
        return []
    permissions = _gh_json(["api", f"repos/{owner}/{repo_name}/actions/permissions"])
    selected = _gh_json(["api", f"repos/{owner}/{repo_name}/actions/permissions/selected-actions"])
    failures: list[str] = []

    for key in ["enabled", "allowed_actions", "sha_pinning_required"]:
        if key in expected and permissions.get(key) != expected[key]:
            failures.append(f"{repo_name}: actions {key} expected {expected[key]!r}, got {permissions.get(key)!r}")

    for key in ["github_owned_allowed", "verified_allowed"]:
        if key in expected and selected.get(key) != expected[key]:
            failures.append(f"{repo_name}: selected actions {key} expected {expected[key]!r}, got {selected.get(key)!r}")

    expected_patterns = set(str(item) for item in expected.get("patterns_allowed", []))
    actual_patterns = set(str(item) for item in selected.get("patterns_allowed", []))
    if expected_patterns != actual_patterns:
        failures.append(
            f"{repo_name}: selected action patterns drift: expected {sorted(expected_patterns)}, "
            f"got {sorted(actual_patterns)}"
        )
    return failures


def _secret_failures(owner: str, repo_name: str, policy: dict[str, Any]) -> list[str]:
    required = {str(item) for item in policy.get("required_secrets", [])}
    if not required:
        return []
    data = _gh_json(["secret", "list", "--repo", f"{owner}/{repo_name}", "--json", "name"])
    present = {str(item["name"]) for item in data}
    missing = sorted(required - present)
    return [f"{repo_name}: missing required repository secret {name}" for name in missing]


def _gh_json(args: list[str]) -> Any:
    result = subprocess.run(["gh", *args], check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"gh {' '.join(args)} failed")
    text = result.stdout.strip()
    return json.loads(text) if text else None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
