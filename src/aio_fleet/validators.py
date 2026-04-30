from __future__ import annotations

import re
import tomllib
from pathlib import Path
from urllib.parse import urlparse
from xml.etree.ElementTree import Element, ParseError  # nosec B405

import defusedxml.ElementTree as DefusedET

from aio_fleet.manifest import FleetManifest, RepoConfig

PINNED_REUSABLE_WORKFLOW = re.compile(
    r"uses:\s+JSONbored/aio-fleet/\.github/workflows/aio-[a-z-]+\.yml@([0-9a-f]{40})"
)

ACTION_REF = re.compile(r"^\s*(?:-\s*)?uses:\s*([^@\s]+)@([^\s#]+)", re.MULTILINE)
SHA_REF = re.compile(r"^[0-9a-f]{40}$")
CATALOG_RAW_PREFIX = "https://raw.githubusercontent.com/"

DERIVED_REQUIRED_FILES = [
    "Dockerfile",
    "README.md",
    "pyproject.toml",
    "tests/template/test_validate_template.py",
    "tests/integration/test_container_runtime.py",
    "scripts/validate-template.py",
    "scripts/update-template-changes.py",
    ".github/FUNDING.yml",
    "SECURITY.md",
    ".github/pull_request_template.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/installation_help.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    "renovate.json",
]

PLACEHOLDER_CHECKS = [
    (
        "Dockerfile",
        "Replace this starter base with the real upstream image once the derived repo is wired.",
    ),
]

XML_PLACEHOLDER_CHECKS = [
    "yourapp-aio",
    "Replace this overview with the real app description and first-run guidance.",
    "replace-with-real-search-terms",
    "Replace this with any real operational prerequisites or remove it.",
    "https://github.com/JSONbored/yourapp-aio/releases",
]

SERVICE_PLACEHOLDER_FILES = [
    "rootfs/etc/services.d/app/run",
    "rootfs/usr/local/bin/aio-template-app.py",
]


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
            failures.append(
                f"{repo.name}: catalog_assets entries require source and target"
            )
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
    catalog_repo = str(
        manifest.raw.get("awesome_unraid_repository", "JSONbored/awesome-unraid")
    )

    for source, target in xml_assets:
        root = _parse_xml(repo, source, failures)
        if root is None:
            continue

        for field in [
            "Name",
            "Project",
            "Support",
            "Overview",
            "Category",
            "TemplateURL",
            "Icon",
        ]:
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
        if icon and not icon.startswith(
            f"{CATALOG_RAW_PREFIX}{catalog_repo}/main/icons/"
        ):
            failures.append(
                f"{repo.name}: {source} Icon must point at {catalog_repo}/main/icons/"
            )

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


def derived_repo_failures(
    repo_path: Path,
    *,
    strict_placeholders: bool = False,
    template_xml: str | None = None,
) -> list[str]:
    failures: list[str] = []
    repo_path = repo_path.resolve()

    for required in DERIVED_REQUIRED_FILES:
        _require_file(repo_path, required, failures)
    if (repo_path / "components.toml").exists():
        _require_file(repo_path, "scripts/components.py", failures)
    _require_absent(repo_path, ".github/CODEOWNERS", failures)

    template_xml = template_xml or _effective_template_xml(repo_path)
    component_templates = _component_templates(repo_path, failures)
    is_template_repo = _is_template_repo(repo_path)

    if template_xml:
        _require_file(repo_path, template_xml, failures)
        if not is_template_repo:
            _require_absent(repo_path, "template-aio.xml", failures)

    xml_files = [
        template
        for template in component_templates
        if _require_file(repo_path, template, failures)
    ]
    if template_xml and (repo_path / template_xml).is_file():
        xml_files.append(template_xml)

    if strict_placeholders:
        for relative_path, placeholder in PLACEHOLDER_CHECKS:
            _check_no_placeholder(repo_path, placeholder, [relative_path], failures)
        for placeholder in XML_PLACEHOLDER_CHECKS:
            _check_no_placeholder(repo_path, placeholder, xml_files, failures)
        _check_no_placeholder(
            repo_path,
            "aio-template starter app",
            SERVICE_PLACEHOLDER_FILES,
            failures,
        )

    return failures


def catalog_repo_failures(manifest: FleetManifest, catalog_path: Path) -> list[str]:
    failures: list[str] = []
    catalog_repo = str(
        manifest.raw.get("awesome_unraid_repository", "JSONbored/awesome-unraid")
    )

    if not catalog_path.exists():
        return [f"catalog path missing: {catalog_path}"]

    for repo in manifest.repos.values():
        if repo.raw.get("catalog_published") is False:
            for _source, target in _catalog_xml_assets(repo):
                if (catalog_path / target).exists():
                    failures.append(
                        f"{repo.name}: catalog target exists while catalog_published is false: {target}"
                    )
            continue
        for source, target in _catalog_xml_assets(repo):
            xml_path = catalog_path / target
            if not xml_path.exists():
                failures.append(f"{repo.name}: catalog target missing: {target}")
                continue
            root = _parse_catalog_xml(repo.name, target, xml_path, failures)
            if root is None:
                continue

            for field in [
                "Name",
                "Project",
                "Support",
                "Overview",
                "Category",
                "TemplateURL",
                "Icon",
            ]:
                if not (root.findtext(field) or "").strip():
                    failures.append(
                        f"{repo.name}: catalog {target} missing non-empty <{field}>"
                    )

            expected_template_url = f"{CATALOG_RAW_PREFIX}{catalog_repo}/main/{target}"
            template_url = (root.findtext("TemplateURL") or "").strip()
            if template_url != expected_template_url:
                failures.append(
                    f"{repo.name}: catalog {target} TemplateURL must be {expected_template_url}, got {template_url}"
                )

            icon_target = catalog_target_from_icon(root.findtext("Icon") or "")
            if icon_target and not (catalog_path / icon_target).exists():
                failures.append(
                    f"{repo.name}: catalog {target} icon missing: {icon_target}"
                )

            if repo.path.exists() and source and not (repo.path / source).exists():
                failures.append(
                    f"{repo.name}: source XML missing for catalog target {target}: {source}"
                )

    return failures


def _require_file(repo_path: Path, relative_path: str, failures: list[str]) -> bool:
    if not (repo_path / relative_path).is_file():
        failures.append(f"missing required file: {relative_path}")
        return False
    return True


def _require_absent(repo_path: Path, relative_path: str, failures: list[str]) -> None:
    if (repo_path / relative_path).exists():
        failures.append(
            f"remove template placeholder path in derived repo: {relative_path}"
        )


def _effective_template_xml(repo_path: Path) -> str:
    root_xml_files = sorted(
        path.name for path in repo_path.glob("*.xml") if path.is_file()
    )
    inferred_repo_xml = f"{repo_path.name}.xml"
    if (repo_path / inferred_repo_xml).is_file():
        return inferred_repo_xml
    return root_xml_files[0] if len(root_xml_files) == 1 else ""


def _component_templates(repo_path: Path, failures: list[str]) -> list[str]:
    components_path = repo_path / "components.toml"
    if not components_path.exists():
        return []
    try:
        data = tomllib.loads(components_path.read_text())
    except tomllib.TOMLDecodeError as exc:
        failures.append(f"unable to parse components.toml: {exc}")
        return []
    components = data.get("components", {})
    if not isinstance(components, dict):
        failures.append("components.toml must contain a [components] table")
        return []
    templates: list[str] = []
    for name, config in components.items():
        if not isinstance(config, dict):
            failures.append(f"components.toml component {name} must be a table")
            continue
        template = str(config.get("template", "")).strip()
        if template:
            templates.append(template)
    return templates


def _is_template_repo(repo_path: Path) -> bool:
    workflow = repo_path / ".github" / "workflows" / "publish-release.yml"
    return workflow.exists() and "Publish Release / Template" in workflow.read_text()


def _check_no_placeholder(
    repo_path: Path,
    placeholder: str,
    relative_paths: list[str],
    failures: list[str],
) -> None:
    existing_paths = [
        repo_path / path for path in relative_paths if (repo_path / path).exists()
    ]
    for path in existing_paths:
        if placeholder in path.read_text(errors="ignore"):
            failures.append(
                f"found unresolved placeholder '{placeholder}' in: {path.relative_to(repo_path)}"
            )


def _catalog_xml_assets(repo: RepoConfig) -> list[tuple[str, str]]:
    assets = repo.raw.get("catalog_assets", [])
    if not isinstance(assets, list):
        return []
    return [
        (str(asset.get("source", "")).strip(), str(asset.get("target", "")).strip())
        for asset in assets
        if isinstance(asset, dict)
        and str(asset.get("target", "")).strip().endswith(".xml")
    ]


def _parse_xml(repo: RepoConfig, source: str, failures: list[str]) -> Element | None:
    xml_path = repo.path / source
    if not xml_path.exists():
        return None
    try:
        return DefusedET.parse(xml_path).getroot()
    except ParseError as exc:
        failures.append(f"{repo.name}: unable to parse catalog XML {source}: {exc}")
        return None


def _parse_catalog_xml(
    repo_name: str,
    target: str,
    xml_path: Path,
    failures: list[str],
) -> Element | None:
    try:
        return DefusedET.parse(xml_path).getroot()
    except ParseError as exc:
        failures.append(f"{repo_name}: unable to parse catalog XML {target}: {exc}")
        return None


def _platforms(repo: RepoConfig) -> set[str]:
    return {
        item.strip()
        for item in str(repo.get("publish_platforms", "")).split(",")
        if item.strip()
    }


def _dockerfile_mentions_arm64(text: str) -> bool:
    return bool(re.search(r"\barm64\b|\baarch64\b", text))


def _dockerfile_mentions_amd64(text: str) -> bool:
    return bool(re.search(r"\bamd64\b|\bx86_64\b", text))
