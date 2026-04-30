from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

from aio_fleet.manifest import FleetManifest, RepoConfig

PINNED_REUSABLE_WORKFLOW = re.compile(
    r"uses:\s+JSONbored/aio-fleet/\.github/workflows/aio-[a-z-]+\.yml@([0-9a-f]{40})"
)

ACTION_REF = re.compile(r"^\s*(?:-\s*)?uses:\s*([^@\s]+)@([^\s#]+)", re.MULTILINE)
SHA_REF = re.compile(r"^[0-9a-f]{40}$")
CATALOG_RAW_PREFIX = "https://raw.githubusercontent.com/"


def catalog_target_from_icon(icon: str) -> str | None:
    value = icon.strip()
    if not value:
        return None
    if value.startswith("icons/"):
        return value

    parsed = urlparse(value)
    path = parsed.path.lstrip("/")
    for marker in ("/main/", "/master/"):
        if marker in path:
            target = path.partition(marker)[2]
            return target if target.startswith("icons/") else None
    return None


def catalog_asset_failures(repo: RepoConfig) -> list[str]:
    assets = repo.raw.get("catalog_assets", [])
    if not isinstance(assets, list):
        return [f"{repo.name}: catalog_assets must be a list"]

    failures: list[str] = []
    target_sources: dict[str, str] = {}
    xml_sources: list[str] = []
    xml_icon_targets: set[str] = set()

    for asset in assets:
        if not isinstance(asset, dict):
            failures.append(f"{repo.name}: catalog_assets entries must be mappings")
            continue
        source = str(asset.get("source", "")).strip()
        target = str(asset.get("target", "")).strip()
        if not source or not target:
            failures.append(f"{repo.name}: catalog_assets entries require source and target")
            continue

        target_sources[target] = source
        if target.endswith(".xml"):
            xml_sources.append(source)
        if not (repo.path / source).exists():
            failures.append(f"{repo.name}: catalog_assets source missing: {source}")

    for source in xml_sources:
        root = _parse_xml(repo, source, failures)
        if root is None:
            continue

        icon_target = catalog_target_from_icon(root.findtext("Icon") or "")
        if icon_target:
            xml_icon_targets.add(icon_target)

    for target in sorted(target_sources):
        if not target.startswith("icons/"):
            continue
        if target not in xml_icon_targets:
            failures.append(
                f"{repo.name}: catalog_assets target {target} is not referenced by any catalog XML Icon"
            )

    return failures


def template_metadata_failures(repo: RepoConfig, manifest: FleetManifest) -> list[str]:
    failures: list[str] = []
    xml_assets = _catalog_xml_assets(repo)
    catalog_repo = str(manifest.raw.get("awesome_unraid_repository", "JSONbored/awesome-unraid"))

    for source, target in xml_assets:
        root = _parse_xml(repo, source, failures)
        if root is None:
            continue

        for field in ["Name", "Project", "Support", "Overview", "Category", "TemplateURL", "Icon"]:
            if not (root.findtext(field) or "").strip():
                failures.append(f"{repo.name}: {source} missing non-empty <{field}>")

        if repo.publish_profile == "template":
            continue

        template_url = (root.findtext("TemplateURL") or "").strip()
        expected_template_url = f"{CATALOG_RAW_PREFIX}{catalog_repo}/main/{target}"
        if template_url != expected_template_url:
            failures.append(
                f"{repo.name}: {source} TemplateURL must be {expected_template_url}, got {template_url}"
            )

        icon = (root.findtext("Icon") or "").strip()
        if icon and not icon.startswith(f"{CATALOG_RAW_PREFIX}{catalog_repo}/main/icons/"):
            failures.append(f"{repo.name}: {source} Icon must point at {catalog_repo}/main/icons/")

    return failures


def publish_platform_failures(repo: RepoConfig) -> list[str]:
    platforms = _platforms(repo)
    failures: list[str] = []
    dockerfiles = [repo.path / "Dockerfile"]
    if repo.is_signoz_suite:
        agent_dockerfile = str(repo.raw["components"]["agent"].get("dockerfile", ""))
        if agent_dockerfile:
            dockerfiles.append(repo.path / agent_dockerfile)

    for dockerfile in dockerfiles:
        if not dockerfile.exists():
            continue
        text = dockerfile.read_text()
        if "TARGETARCH" not in text:
            continue
        rel = dockerfile.relative_to(repo.path)
        if "linux/arm64" in platforms and not _dockerfile_mentions_arm64(text):
            failures.append(
                f"{repo.name}: {rel} does not appear to handle arm64 but publish_platforms includes linux/arm64"
            )
        if "linux/amd64" in platforms and not _dockerfile_mentions_amd64(text):
            failures.append(
                f"{repo.name}: {rel} does not appear to handle amd64 but publish_platforms includes linux/amd64"
            )

    return failures


def pinned_action_failures(repo_path: Path) -> list[str]:
    workflow_paths = [
        *repo_path.joinpath(".github", "workflows").glob("*.yml"),
        *repo_path.joinpath(".github", "actions").glob("*/action.yml"),
    ]
    failures: list[str] = []

    for path in sorted(workflow_paths):
        for match in ACTION_REF.finditer(path.read_text()):
            target, ref = match.groups()
            if target.startswith("./"):
                continue
            if not SHA_REF.fullmatch(ref):
                failures.append(
                    f"{path.relative_to(repo_path)}: action is not pinned to a full SHA -> {target}@{ref}"
                )

    return failures


def repo_policy_failures(repo: RepoConfig, manifest: FleetManifest) -> list[str]:
    return [
        *catalog_asset_failures(repo),
        *template_metadata_failures(repo, manifest),
        *publish_platform_failures(repo),
        *pinned_action_failures(repo.path),
    ]


def catalog_repo_failures(manifest: FleetManifest, catalog_path: Path) -> list[str]:
    failures: list[str] = []
    catalog_repo = str(manifest.raw.get("awesome_unraid_repository", "JSONbored/awesome-unraid"))

    if not catalog_path.exists():
        return [f"catalog path missing: {catalog_path}"]

    for repo in manifest.repos.values():
        if repo.raw.get("catalog_published") is False:
            continue
        for source, target in _catalog_xml_assets(repo):
            xml_path = catalog_path / target
            if not xml_path.exists():
                failures.append(f"{repo.name}: catalog target missing: {target}")
                continue
            root = _parse_catalog_xml(repo.name, target, xml_path, failures)
            if root is None:
                continue

            for field in ["Name", "Project", "Support", "Overview", "Category", "TemplateURL", "Icon"]:
                if not (root.findtext(field) or "").strip():
                    failures.append(f"{repo.name}: catalog {target} missing non-empty <{field}>")

            expected_template_url = f"{CATALOG_RAW_PREFIX}{catalog_repo}/main/{target}"
            template_url = (root.findtext("TemplateURL") or "").strip()
            if template_url != expected_template_url:
                failures.append(
                    f"{repo.name}: catalog {target} TemplateURL must be {expected_template_url}, got {template_url}"
                )

            icon_target = catalog_target_from_icon(root.findtext("Icon") or "")
            if icon_target and not (catalog_path / icon_target).exists():
                failures.append(f"{repo.name}: catalog {target} icon missing: {icon_target}")

            if source and not (repo.path / source).exists():
                failures.append(f"{repo.name}: source XML missing for catalog target {target}: {source}")

    return failures


def _catalog_xml_assets(repo: RepoConfig) -> list[tuple[str, str]]:
    assets = repo.raw.get("catalog_assets", [])
    if not isinstance(assets, list):
        return []
    return [
        (str(asset.get("source", "")).strip(), str(asset.get("target", "")).strip())
        for asset in assets
        if isinstance(asset, dict) and str(asset.get("target", "")).strip().endswith(".xml")
    ]


def _parse_xml(repo: RepoConfig, source: str, failures: list[str]) -> ET.Element | None:
    xml_path = repo.path / source
    if not xml_path.exists():
        return None
    try:
        return ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        failures.append(f"{repo.name}: unable to parse catalog XML {source}: {exc}")
        return None


def _parse_catalog_xml(
    repo_name: str,
    target: str,
    xml_path: Path,
    failures: list[str],
) -> ET.Element | None:
    try:
        return ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        failures.append(f"{repo_name}: unable to parse catalog XML {target}: {exc}")
        return None


def _platforms(repo: RepoConfig) -> set[str]:
    return {item.strip() for item in str(repo.get("publish_platforms", "")).split(",") if item.strip()}


def _dockerfile_mentions_arm64(text: str) -> bool:
    return bool(re.search(r"\barm64\b|\baarch64\b", text))


def _dockerfile_mentions_amd64(text: str) -> bool:
    return bool(re.search(r"\bamd64\b|\bx86_64\b", text))
