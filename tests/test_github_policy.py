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
    allow_rebase_merge: false
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
    required_check_app_id: 12345
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
                "allow_rebase_merge": True,
            }
        if joined == "api repos/JSONbored/example-aio/branches/main/protection":
            return {
                "required_status_checks": {
                    "contexts": [],
                    "checks": [],
                    "strict": True,
                },
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
        "allow_rebase_merge expected False" in failure for failure in failures
    )  # nosec B101
    assert any(
        "required check 'aio-build / validate-template' app_id expected 12345"
        in failure
        for failure in failures
    )  # nosec B101
    assert any(
        "selected action patterns drift" in failure for failure in failures
    )  # nosec B101


def test_validate_github_policy_accepts_required_check_app_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    policy = tmp_path / "github-policy.yml"
    policy.write_text("""
owner: JSONbored
defaults:
  repository:
    visibility: public
    allow_rebase_merge: false
  branch_protection:
    strict_required_status_checks: true
    require_signed_commits: true
  actions:
    enabled: true
repositories:
  example-aio:
    required_checks:
      - aio-fleet / required
    required_check_app_id: 3565017
""")

    def fake_gh_json(args: list[str]) -> Any:
        joined = " ".join(args)
        if joined == "api repos/JSONbored/example-aio":
            return {
                "visibility": "public",
                "allow_rebase_merge": False,
            }
        if joined == "api repos/JSONbored/example-aio/branches/main/protection":
            return {
                "required_status_checks": {
                    "contexts": ["aio-fleet / required"],
                    "checks": [{"context": "aio-fleet / required", "app_id": 3565017}],
                    "strict": True,
                },
                "required_signatures": {"enabled": True},
            }
        if joined == "api repos/JSONbored/example-aio/actions/permissions":
            return {"enabled": True}
        if (
            joined
            == "api repos/JSONbored/example-aio/actions/permissions/selected-actions"
        ):
            return {}
        raise AssertionError(args)

    monkeypatch.setattr(github_policy, "_gh_json", fake_gh_json)

    assert (  # nosec B101
        github_policy.validate_github_policy(
            policy, repos=["example-aio"], check_secrets=False
        )
        == []
    )


def test_validate_github_policy_rejects_same_context_wrong_app_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    policy = tmp_path / "github-policy.yml"
    policy.write_text("""
owner: JSONbored
defaults:
  repository:
    visibility: public
  branch_protection:
    strict_required_status_checks: true
  actions:
    enabled: true
repositories:
  example-aio:
    required_checks:
      - aio-fleet / required
    required_check_app_id: 3565017
""")

    def fake_gh_json(args: list[str]) -> Any:
        joined = " ".join(args)
        if joined == "api repos/JSONbored/example-aio":
            return {"visibility": "public"}
        if joined == "api repos/JSONbored/example-aio/branches/main/protection":
            return {
                "required_status_checks": {
                    "contexts": ["aio-fleet / required"],
                    "checks": [{"context": "aio-fleet / required", "app_id": 999999}],
                    "strict": True,
                }
            }
        if joined == "api repos/JSONbored/example-aio/actions/permissions":
            return {"enabled": True}
        if (
            joined
            == "api repos/JSONbored/example-aio/actions/permissions/selected-actions"
        ):
            return {}
        raise AssertionError(args)

    monkeypatch.setattr(github_policy, "_gh_json", fake_gh_json)

    failures = github_policy.validate_github_policy(
        policy, repos=["example-aio"], check_secrets=False
    )

    assert failures == [  # nosec B101
        "example-aio: required check 'aio-fleet / required' app_id expected 3565017, got 999999"
    ]


def test_infra_uses_rulesets_for_app_bound_required_checks() -> None:
    main_tf = (Path(__file__).resolve().parents[1] / "infra/github/main.tf").read_text()

    assert (  # nosec B101
        'resource "github_repository_ruleset" "trusted_required_checks"' in main_tf
    )
    assert "integration_id = each.value.required_check_app_id" in main_tf  # nosec B101
    assert 'dynamic "required_status_checks"' in main_tf  # nosec B101
    assert "required_check_app_id == null" in main_tf  # nosec B101
