from __future__ import annotations

from pathlib import Path
from typing import Any

from aio_fleet import github_policy


def test_validate_github_policy_detects_required_check_and_action_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    policy = tmp_path / "github-policy.yml"
    policy.write_text("""
owner: JSONbored
defaults:
  repository:
    visibility: public
    homepage_url: https://aethereal.dev
    has_issues: true
    has_projects: false
    has_wiki: false
    delete_branch_on_merge: true
    allow_auto_merge: false
  branch_protection:
    strict_required_status_checks: true
    enforce_admins: false
    require_conversation_resolution: true
    require_signed_commits: true
    required_approving_review_count: 0
  actions:
    enabled: true
    allowed_actions: selected
    github_owned_allowed: true
    verified_allowed: true
    sha_pinning_required: true
    patterns_allowed:
      - JSONbored/aio-fleet/.github/workflows/aio-*.yml@*
repositories:
  example-aio:
    required_checks:
      - aio-build / validate-template
""")

    def fake_gh_json(args: list[str]) -> Any:
        joined = " ".join(args)
        if joined == "api repos/JSONbored/example-aio":
            return {
                "visibility": "public",
                "homepage": "https://aethereal.dev",
                "has_issues": True,
                "has_projects": False,
                "has_wiki": False,
                "delete_branch_on_merge": True,
                "allow_auto_merge": False,
            }
        if joined == "api repos/JSONbored/example-aio/branches/main/protection":
            return {
                "required_status_checks": {"contexts": [], "strict": True},
                "enforce_admins": {"enabled": False},
                "required_conversation_resolution": {"enabled": True},
                "required_signatures": {"enabled": True},
                "required_pull_request_reviews": {"required_approving_review_count": 0},
            }
        if joined == "api repos/JSONbored/example-aio/actions/permissions":
            return {
                "enabled": True,
                "allowed_actions": "selected",
                "sha_pinning_required": True,
            }
        if (
            joined
            == "api repos/JSONbored/example-aio/actions/permissions/selected-actions"
        ):
            return {
                "github_owned_allowed": True,
                "verified_allowed": True,
                "patterns_allowed": [],
            }
        raise AssertionError(args)

    monkeypatch.setattr(github_policy, "_gh_json", fake_gh_json)

    failures = github_policy.validate_github_policy(
        policy, repos=["example-aio"], check_secrets=False
    )

    assert any("required checks drift" in failure for failure in failures)  # nosec B101
    assert any(
        "selected action patterns drift" in failure for failure in failures
    )  # nosec B101
