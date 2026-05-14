from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

import yaml


def poll_outputs(
    *, report_path: Path, run_checks: bool, github_output: Path | None
) -> dict[str, Any]:
    payload = _read_json(report_path, default={"targets": []})
    targets = payload.get("targets", [])
    targets = targets if isinstance(targets, list) else []
    output = {
        "run_checks": run_checks,
        "has_targets": bool(targets),
        "targets": targets,
    }
    if github_output:
        with github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"run_checks={'true' if run_checks else 'false'}\n")
            handle.write(f"has_targets={'true' if targets else 'false'}\n")
            handle.write("targets<<__AIO_FLEET_TARGETS__\n")
            handle.write(json.dumps(targets, sort_keys=True))
            handle.write("\n__AIO_FLEET_TARGETS__\n")
    return output


def render_upstream_summary(*, report_path: Path, output_path: Path | None) -> str:
    if not report_path.exists():
        text = "No upstream report was generated.\n"
        if output_path:
            output_path.write_text(text)
        return text
    report = _read_json(report_path, default={"repos": []})
    lines = ["# Upstream Monitor", ""]
    for item in report.get("repos", []):
        if not isinstance(item, dict):
            continue
        repo = item.get("repo", "unknown")
        if item.get("skipped"):
            lines.append(f"- `{repo}`: skipped ({item['skipped']})")
            continue
        if item.get("error"):
            lines.append(f"- `{repo}`: failed ({item['error']})")
            continue
        updates = [
            result
            for result in item.get("results", [])
            if isinstance(result, dict) and result.get("updates_available")
        ]
        blocked = [
            result
            for result in item.get("results", [])
            if isinstance(result, dict)
            and (result.get("blocked") or result.get("state") == "blocked")
        ]
        state = "blocked" if blocked else "updates available" if updates else "current"
        lines.append(f"- `{repo}`: {state}")
        for result in blocked:
            lines.append(
                "  - `{component}`: {current_version} -> {latest_version} "
                "blocked: {blocked_reason}; next: {next_action}".format(**result)
            )
        for result in updates:
            if result in blocked:
                continue
            lines.append(
                f"  - `{result['component']}`: {result['current_version']} -> {result['latest_version']}"
            )
    text = "\n".join(lines) + "\n"
    if output_path:
        output_path.write_text(text)
    return text


def render_registry_summary(
    *, report_path: Path, status: str, output_path: Path | None
) -> str:
    lines = ["# Registry Audit", "", f"Verify command exit status: `{status}`", ""]
    if not report_path.exists():
        lines.extend(["Registry report was not generated.", ""])
        text = "\n".join(lines)
        if output_path:
            output_path.write_text(text)
        return text
    report = _read_json(report_path, default={"repos": []})
    lines.extend(
        [
            "| Repo | Component | SHA | State | Docker Hub tags | GHCR tags |",
            "| --- | --- | --- | --- | ---: | ---: |",
        ]
    )
    failures: list[str] = []
    for item in report.get("repos", []):
        if not isinstance(item, dict):
            continue
        repo_failures = item.get("failures", [])
        state = (
            f"skipped:{item['skipped']}"
            if item.get("skipped")
            else "failed" if repo_failures else "ok"
        )
        sha = str(item.get("sha", ""))[:12]
        lines.append(
            "| {repo} | {component} | `{sha}` | {state} | {dockerhub} | {ghcr} |".format(
                repo=item.get("repo", ""),
                component=item.get("component", "aio"),
                sha=sha,
                state=state,
                dockerhub=len(item.get("dockerhub", [])),
                ghcr=len(item.get("ghcr", [])),
            )
        )
        for failure in repo_failures if isinstance(repo_failures, list) else []:
            failures.append(f"{item.get('repo', '')}: {failure}")
    if failures:
        lines.extend(["", "## Missing Or Failed Tags", ""])
        lines.extend(f"- `{failure}`" for failure in failures)
    else:
        lines.extend(["", "All expected registry tags resolved."])
    text = "\n".join(lines) + "\n"
    if output_path:
        output_path.write_text(text)
    return text


def checkout_dashboard_repos(
    *,
    manifest_path: Path,
    checkout_root: Path,
    output_manifest: Path,
    token: str,
) -> dict[str, Any]:
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    owner = manifest["owner"]
    checkout_root = checkout_root.resolve()
    checkout_root.mkdir(exist_ok=True)
    refs: list[tuple[str, str, Path]] = []
    for name, repo_config in manifest.get("repos", {}).items():
        path = checkout_root / name
        repo_config["path"] = str(path)
        refs.append((name, repo_config.get("github_repo", f"{owner}/{name}"), path))
    dashboard = manifest.get("dashboard", {})
    if isinstance(dashboard, dict):
        for group in ("destination_repos", "rehab_repos"):
            for name, repo_config in dashboard.get(group, {}).items():
                if not isinstance(repo_config, dict):
                    continue
                path = checkout_root / name
                repo_config["path"] = str(path)
                if repo_config.get("catalog_path"):
                    repo_config["catalog_path"] = str(path)
                refs.append(
                    (name, repo_config.get("github_repo", f"{owner}/{name}"), path)
                )
    results = _checkout_refs(refs, token=token, submodules="best-effort")
    output_manifest.write_text(yaml.safe_dump(manifest, sort_keys=False))
    return {"repos": results, "manifest": str(output_manifest)}


def upstream_monitor_checkouts(
    *,
    manifest_path: Path,
    checkout_root: Path,
    output_path: Path,
    token: str,
    mutate: bool,
    dry_run: bool,
) -> dict[str, Any]:
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    owner = manifest["owner"]
    checkout_root.mkdir(exist_ok=True)
    report: dict[str, Any] = {"repos": []}
    status = 0
    for repo, repo_config in manifest["repos"].items():
        if repo_config.get("publish_profile") == "template":
            report["repos"].append({"repo": repo, "skipped": "manual-template"})
            continue
        worktree = checkout_root / repo
        checkout = _checkout_refs(
            [(repo, repo_config.get("github_repo", f"{owner}/{repo}"), worktree)],
            token=token,
            submodules="required",
        )[0]
        if checkout.get("error"):
            status = 1
            report["repos"].append({"repo": repo, "error": checkout["error"]})
            continue
        args = [
            sys.executable,
            "-m",
            "aio_fleet",
            "upstream",
            "monitor",
            "--repo",
            repo,
            "--repo-path",
            str(worktree),
            "--format",
            "json",
        ]
        if mutate:
            args.extend(["--write", "--create-pr", "--post-check"])
        if dry_run:
            args.append("--dry-run")
        run = subprocess.run(  # nosec B603
            args, check=False, text=True, capture_output=True
        )
        if run.stderr:
            print(run.stderr, file=sys.stderr, end="")
        if run.returncode != 0:
            status = 1
        try:
            parsed = json.loads(run.stdout)
        except json.JSONDecodeError:
            status = 1
            report["repos"].append(
                {
                    "repo": repo,
                    "error": (run.stdout or run.stderr).strip()
                    or "upstream monitor produced invalid JSON",
                }
            )
            continue
        report["repos"].extend(parsed.get("repos", []))
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    report["status"] = status
    return report


def registry_audit_checkouts(
    *,
    manifest_path: Path,
    checkout_root: Path,
    output_path: Path,
    token: str,
    github_output: Path | None,
) -> dict[str, Any]:
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    owner = manifest["owner"]
    checkout_root.mkdir(exist_ok=True)
    report: dict[str, Any] = {"repos": []}
    status = 0
    for repo, repo_config in manifest["repos"].items():
        if repo_config.get("publish_profile") == "template":
            report["repos"].append(
                {
                    "repo": repo,
                    "sha": "",
                    "dockerhub": [],
                    "ghcr": [],
                    "failures": [],
                    "skipped": "manual-template-publish",
                }
            )
            continue
        worktree = checkout_root / repo
        checkout = _checkout_refs(
            [(repo, repo_config.get("github_repo", f"{owner}/{repo}"), worktree)],
            token=token,
            submodules="none",
        )[0]
        if checkout.get("error"):
            status = 1
            report["repos"].append(
                {
                    "repo": repo,
                    "sha": "",
                    "dockerhub": [],
                    "ghcr": [],
                    "failures": [checkout["error"]],
                }
            )
            continue
        sha = subprocess.check_output(  # nosec B603 B607
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            env=_minimal_env(),
            text=True,
        ).strip()
        components = _publish_components(repo_config)
        for component in components:
            verify = subprocess.run(  # nosec B603
                [
                    sys.executable,
                    "-m",
                    "aio_fleet",
                    "registry",
                    "verify",
                    "--repo",
                    repo,
                    "--repo-path",
                    str(worktree),
                    "--sha",
                    sha,
                    "--component",
                    component,
                    "--format",
                    "json",
                ],
                env=_verify_env(),
                check=False,
                text=True,
                capture_output=True,
            )
            if verify.stderr:
                print(verify.stderr, file=sys.stderr, end="")
            if verify.returncode != 0:
                status = 1
            try:
                parsed = json.loads(verify.stdout)
            except json.JSONDecodeError:
                status = 1
                report["repos"].append(
                    {
                        "repo": repo,
                        "component": component,
                        "sha": sha,
                        "dockerhub": [],
                        "ghcr": [],
                        "failures": [
                            (verify.stdout or verify.stderr).strip()
                            or "registry verify produced invalid JSON"
                        ],
                    }
                )
                continue
            report["repos"].extend(parsed.get("repos", []))
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if github_output:
        with github_output.open("a", encoding="utf-8") as output:
            output.write(f"status={status}\n")
    report["status"] = status
    return report


def _checkout_refs(
    refs: list[tuple[str, str, Path]], *, token: str, submodules: str
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    env = _git_auth_env(token)
    seen: set[tuple[str, str]] = set()
    for name, github_repo, path in refs:
        key = (github_repo, str(path))
        if key in seen:
            continue
        seen.add(key)
        if path.exists():
            shutil.rmtree(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        clone = subprocess.run(  # nosec B603 B607
            ["git", "clone", f"https://github.com/{github_repo}.git", str(path)],
            env=env,
            check=False,
            text=True,
            capture_output=True,
        )
        result: dict[str, Any] = {
            "repo": name,
            "github_repo": github_repo,
            "path": str(path),
        }
        if clone.returncode != 0:
            result["error"] = (clone.stderr or clone.stdout).strip() or "clone failed"
            results.append(result)
            continue
        if submodules != "none":
            update = subprocess.run(  # nosec B603 B607
                [
                    "git",
                    "-C",
                    str(path),
                    "submodule",
                    "update",
                    "--init",
                    "--recursive",
                ],
                env=env,
                check=False,
                text=True,
                capture_output=True,
            )
            if update.returncode != 0:
                detail = (update.stderr or update.stdout).strip()
                if submodules == "required":
                    result["error"] = detail or "submodule update failed"
                else:
                    result["warning"] = detail or "submodule update skipped"
        results.append(result)
    return results


def _git_auth_env(token: str) -> dict[str, str]:
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = _minimal_env()
    env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {auth}",
        }
    )
    return env


def _minimal_env() -> dict[str, str]:
    return {
        key: os.environ[key]
        for key in ("HOME", "PATH", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
        if key in os.environ
    }


def _verify_env() -> dict[str, str]:
    return {
        key: os.environ[key]
        for key in (
            "DOCKER_CONFIG",
            "HOME",
            "PATH",
            "SSL_CERT_FILE",
            "REQUESTS_CA_BUNDLE",
            "XDG_RUNTIME_DIR",
        )
        if key in os.environ
    }


def _publish_components(repo_config: dict[str, Any]) -> list[str]:
    components = repo_config.get("components")
    if not isinstance(components, dict):
        return ["aio"]
    names = [
        name
        for name, config in components.items()
        if name == "aio" or (isinstance(config, dict) and config.get("image_name"))
    ]
    return names or ["aio"]


def _read_json(path: Path, *, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return default
    return data if isinstance(data, dict) else default
