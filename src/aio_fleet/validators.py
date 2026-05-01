from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404
import tomllib
from pathlib import Path
from urllib.parse import urlparse
from xml.etree.ElementTree import Element, ParseError, tostring  # nosec B405

import defusedxml.ElementTree as DefusedET

from aio_fleet.manifest import FleetManifest, RepoConfig

ACTION_REF = re.compile(r"^\s*(?:-\s*)?uses:\s*([^@\s]+)@([^\s#]+)", re.MULTILINE)
SHA_REF = re.compile(r"^[0-9a-f]{40}$")
CATALOG_RAW_PREFIX = "https://raw.githubusercontent.com/"

DERIVED_REQUIRED_FILES = [
    ".aio-fleet.yml",
    "Dockerfile",
    "README.md",
    "pyproject.toml",
    "tests/integration/test_container_runtime.py",
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

TRACKED_ARTIFACT_PATTERNS = [
    ".DS_Store",
    "*.pyc",
    "*/.DS_Store",
    "*/__pycache__/*",
    ".pytest_cache/*",
    ".venv/*",
    ".venv-ci/*",
    ".venv-local/*",
    "infra/github/*.tfstate",
    "infra/github/*.tfstate.*",
    "infra/github/*.tfvars",
]

CHANGELOG_HEADING = re.compile(r"^### \d{4}-\d{2}-\d{2}$")
GENERATED_CHANGELOG_NOTE = (
    "Generated from CHANGELOG.md during release preparation. Do not edit manually."
)
GENERATED_CHANGELOG_BULLET = f"- {GENERATED_CHANGELOG_NOTE}"
LEGACY_CHANGELOG_MARKERS = (
    "[b]Latest release[/b]",
    "GitHub Releases",
    "Full changelog and release notes:",
)
GIT_BIN = shutil.which("git")
UNRAID_CATEGORY_ROOTS = {
    "AI",
    "Backup",
    "Cloud",
    "Downloaders",
    "GameServers",
    "HomeAutomation",
    "MediaApp",
    "MediaServer",
    "Network",
    "Productivity",
    "Security",
    "Tools",
}
UNRAID_CATEGORY_TOKEN = re.compile(
    r"^[A-Za-z][A-Za-z0-9]*(?::[A-Za-z][A-Za-z0-9]*)?:?$"
)

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
            "Repository",
            "Registry",
            "Project",
            "Support",
            "Overview",
            "Category",
            "TemplateURL",
            "Icon",
            "Changes",
        ]:
            if not (root.findtext(field) or "").strip():
                failures.append(f"{repo.name}: {source} missing non-empty <{field}>")

        failures.extend(_repository_registry_failures(repo, source, root))

        if repo.publish_profile == "template":
            continue

        failures.extend(_common_template_quality_failures(repo, source, target, root))
        failures.extend(_generic_xml_failures(repo, source, root))
        failures.extend(_manifest_declared_template_failures(repo, source, root))

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


def tracked_artifact_failures(repo_path: Path) -> list[str]:
    if GIT_BIN is None:
        return [f"{repo_path}: git is required to inspect tracked artifacts"]
    result = subprocess.run(  # nosec B603
        [GIT_BIN, "ls-files", *TRACKED_ARTIFACT_PATTERNS],
        cwd=repo_path,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return [
            f"{repo_path}: unable to inspect tracked artifacts: {result.stderr.strip()}"
        ]
    return [
        f"{repo_path.name}: tracked generated/local artifact should be removed from git: {line.strip()}"
        for line in result.stdout.splitlines()
        if line.strip()
    ]


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
        *runtime_contract_failures(repo),
        *publish_platform_failures(repo),
        *pinned_action_failures(repo.path),
        *tracked_artifact_failures(repo.path),
    ]


def runtime_contract_failures(repo: RepoConfig) -> list[str]:
    failures: list[str] = []
    dockerfile_cache: dict[Path, str] = {}
    for source, _target in _catalog_xml_assets(repo):
        root = _parse_xml(repo, source, failures)
        if root is None:
            continue
        dockerfile = _dockerfile_for_xml(repo, source)
        if not dockerfile.exists():
            failures.append(
                f"{repo.name}: {source} runtime Dockerfile missing: {dockerfile.relative_to(repo.path)}"
            )
            continue
        text = dockerfile_cache.setdefault(dockerfile, dockerfile.read_text())
        relative_dockerfile = dockerfile.relative_to(repo.path)
        failures.extend(
            _dockerfile_runtime_contract_failures(repo, relative_dockerfile, text)
        )
        failures.extend(
            _xml_runtime_contract_failures(
                repo,
                source,
                root,
                relative_dockerfile,
                text,
            )
        )
    return sorted(dict.fromkeys(failures))


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
                "Repository",
                "Registry",
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

            failures.extend(
                _repository_registry_failures(repo, f"catalog {target}", root)
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


def catalog_quality_findings(
    manifest: FleetManifest,
    catalog_path: Path,
) -> list[str]:
    findings = list(catalog_repo_failures(manifest, catalog_path))
    if not catalog_path.exists():
        return findings

    for repo in manifest.repos.values():
        if repo.raw.get("catalog_published") is False:
            continue
        for _source, target in _catalog_xml_assets(repo):
            xml_path = catalog_path / target
            if not xml_path.exists():
                continue
            root = _parse_catalog_xml(repo.name, target, xml_path, findings)
            if root is None:
                continue

            findings.extend(_catalog_xml_quality_findings(repo, target, root))

            icon_target = catalog_target_from_icon(root.findtext("Icon") or "")
            if icon_target:
                findings.extend(
                    _icon_quality_findings(repo.name, icon_target, catalog_path)
                )
    return sorted(dict.fromkeys(findings))


def _catalog_xml_quality_findings(
    repo: RepoConfig, target: str, root: Element
) -> list[str]:
    source = f"catalog {target}"
    findings = _common_template_quality_failures(repo, source, target, root)
    overview = (root.findtext("Overview") or "").strip()
    lower_overview = overview.lower()
    if len(overview) < 500:
        findings.append(
            f"{repo.name}: {source} <Overview> should include fuller CA-facing setup guidance"
        )
    if not any(
        term in lower_overview for term in ["default", "first boot", "first install"]
    ):
        findings.append(
            f"{repo.name}: {source} <Overview> should mention beginner/default setup guidance"
        )
    if not any(
        term in lower_overview
        for term in ["advanced", "power user", "external", "custom"]
    ):
        findings.append(
            f"{repo.name}: {source} <Overview> should mention advanced or power-user configuration"
        )
    return findings


def _icon_quality_findings(
    repo_name: str, icon_target: str, catalog_path: Path
) -> list[str]:
    icon_path = catalog_path / icon_target
    if not icon_path.exists():
        return [f"{repo_name}: catalog icon missing: {icon_target}"]
    try:
        data = icon_path.read_bytes()[:16]
    except OSError as exc:
        return [f"{repo_name}: could not read catalog icon {icon_target}: {exc}"]
    suffix = icon_path.suffix.lower()
    if suffix == ".png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return [f"{repo_name}: catalog icon {icon_target} is not a valid PNG"]
    if suffix in {".jpg", ".jpeg"} and not data.startswith(b"\xff\xd8"):
        return [f"{repo_name}: catalog icon {icon_target} is not a valid JPEG"]
    if suffix not in {".png", ".jpg", ".jpeg", ".svg"}:
        return [f"{repo_name}: catalog icon {icon_target} should be PNG, JPEG, or SVG"]
    return []


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


def _dockerfile_for_xml(repo: RepoConfig, source: str) -> Path:
    components = repo.raw.get("components", {})
    if isinstance(components, dict):
        for component in components.values():
            if not isinstance(component, dict):
                continue
            xml_paths = component.get("xml_paths", [])
            if isinstance(xml_paths, str):
                component_xml_paths = {xml_paths}
            elif isinstance(xml_paths, list):
                component_xml_paths = {str(item) for item in xml_paths}
            else:
                component_xml_paths = set()
            if source in component_xml_paths and component.get("dockerfile"):
                return repo.path / str(component["dockerfile"])
    return repo.path / "Dockerfile"


def _dockerfile_runtime_contract_failures(
    repo: RepoConfig,
    relative_dockerfile: Path,
    text: str,
) -> list[str]:
    failures: list[str] = []
    arg_defaults = _dockerfile_arg_defaults(text)
    from_lines = [
        line.split()[1]
        for line in text.splitlines()
        if line.startswith("FROM ") and len(line.split()) > 1
    ]
    if not from_lines:
        failures.append(f"{repo.name}: {relative_dockerfile} must declare FROM")
    for image in from_lines:
        digest_arg = re.search(r"@\$\{([^}]+)\}", image)
        if "@sha256:" in image:
            continue
        if digest_arg:
            digest_default = arg_defaults.get(digest_arg.group(1), "")
            if digest_default.startswith("sha256:") or "@sha256:" in digest_default:
                continue
        elif any(
            "@sha256:" in arg_defaults.get(name, "")
            for name in re.findall(r"\$\{([^}]+)\}", image)
        ):
            continue
        failures.append(
            f"{repo.name}: {relative_dockerfile} FROM image must be digest-pinned: {image}"
        )

    markers = ["HEALTHCHECK", "curl -fsS", "ENTRYPOINT ["]
    if relative_dockerfile == Path("Dockerfile"):
        markers.extend(
            [
                'ENTRYPOINT ["/init"]',
                "S6_CMD_WAIT_FOR_SERVICES_MAXTIME",
                "S6_BEHAVIOUR_IF_STAGE2_FAILS=2",
            ]
        )
    for marker in markers:
        if marker not in text:
            failures.append(
                f"{repo.name}: {relative_dockerfile} missing runtime safety marker: {marker}"
            )
    return failures


def _xml_runtime_contract_failures(
    repo: RepoConfig,
    source: str,
    root: Element,
    relative_dockerfile: Path,
    dockerfile_text: str,
) -> list[str]:
    failures: list[str] = []
    volumes = _dockerfile_volumes(dockerfile_text)
    exposed_ports = _dockerfile_exposed_ports(dockerfile_text)

    for config in root.findall(".//Config"):
        target = (config.attrib.get("Target") or "").strip()
        if not target:
            continue
        if config.attrib.get("Type") == "Port" and target not in exposed_ports:
            failures.append(
                f"{repo.name}: {source} port target {target} is not exposed by {relative_dockerfile}"
            )
        if target == "/var/run/docker.sock":
            description = (config.attrib.get("Description") or "").lower()
            if config.attrib.get("Display") != "advanced":
                failures.append(
                    f"{repo.name}: {source} Docker socket mount must be advanced"
                )
            if config.attrib.get("Required") != "false":
                failures.append(
                    f"{repo.name}: {source} Docker socket mount must be optional"
                )
            if not any(term in description for term in ["socket", "docker"]) or (
                "security" not in description and "control access" not in description
            ):
                failures.append(
                    f"{repo.name}: {source} Docker socket mount must document security impact"
                )
        if (
            config.attrib.get("Type") != "Path"
            or config.attrib.get("Required") != "true"
        ):
            continue
        default = config.attrib.get("Default") or config.text or ""
        if not default.startswith("/mnt/user/appdata"):
            continue
        if not volumes:
            failures.append(
                f"{repo.name}: {relative_dockerfile} must declare VOLUME for required appdata paths"
            )
            continue
        if not any(
            target == volume or target.startswith(f"{volume.rstrip('/')}/")
            for volume in volumes
        ):
            failures.append(
                f"{repo.name}: {source} required path target {target} is not covered by {relative_dockerfile} VOLUME"
            )

    return failures


def _dockerfile_arg_defaults(text: str) -> dict[str, str]:
    defaults: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("ARG ") or "=" not in line:
            continue
        name, value = line.removeprefix("ARG ").split("=", 1)
        defaults[name] = value
    return defaults


def _dockerfile_volumes(text: str) -> set[str]:
    volumes: set[str] = set()
    for match in re.finditer(r"(?m)^VOLUME\s+(\[[^\]]+\])", text):
        try:
            raw = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(raw, list):
            volumes.update(str(item) for item in raw)
    return volumes


def _dockerfile_exposed_ports(text: str) -> set[str]:
    ports: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("EXPOSE "):
            continue
        for token in line.split()[1:]:
            ports.add(token.split("/", 1)[0])
    return ports


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


def _generic_xml_failures(repo: RepoConfig, source: str, root: Element) -> list[str]:
    failures: list[str] = []
    changes = (root.findtext("Changes") or "").strip()
    if changes:
        first_line = changes.splitlines()[0].strip()
        if not CHANGELOG_HEADING.match(first_line):
            failures.append(
                f"{repo.name}: {source} <Changes> must start with '### YYYY-MM-DD'"
            )
        failures.extend(_generated_changes_failures(repo, source, changes))

    for config in root.findall(".//Config"):
        options = config.findall("Option")
        if not options:
            failures.extend(_pipe_default_failures(repo, source, config))
        else:
            name = config.attrib.get("Name", config.attrib.get("Target", "<unnamed>"))
            failures.append(
                f"{repo.name}: {source} Config {name} uses nested <Option> tags; use pipe-delimited values instead"
            )

    xml_text = tostring(root, encoding="unicode")
    for placeholder in XML_PLACEHOLDER_CHECKS:
        if placeholder in xml_text:
            failures.append(
                f"{repo.name}: {source} contains unresolved placeholder text: {placeholder}"
            )
    return failures


def _generated_changes_failures(
    repo: RepoConfig, source: str, changes: str
) -> list[str]:
    failures: list[str] = []
    for marker in LEGACY_CHANGELOG_MARKERS:
        if marker in changes:
            failures.append(
                f"{repo.name}: {source} <Changes> still uses legacy release text: {marker}"
            )

    lines = [line.strip() for line in changes.splitlines() if line.strip()]
    if len(lines) < 3:
        failures.append(
            f"{repo.name}: {source} <Changes> must include a date heading, generated note, and at least one bullet"
        )
        return failures
    if lines[1] != GENERATED_CHANGELOG_BULLET:
        failures.append(
            f"{repo.name}: {source} <Changes> second line must be '{GENERATED_CHANGELOG_BULLET}'"
        )
    invalid_lines = [line for line in lines[1:] if not line.startswith("- ")]
    if invalid_lines:
        failures.append(
            f"{repo.name}: {source} <Changes> must use bullet lines after the heading; found {invalid_lines[0]!r}"
        )
    return failures


def _pipe_default_failures(repo: RepoConfig, source: str, config: Element) -> list[str]:
    default = config.attrib.get("Default", "")
    if "|" not in default:
        return []

    name = config.attrib.get("Name", config.attrib.get("Target", "<unnamed>"))
    allowed_values = default.split("|")
    if any(value == "" for value in allowed_values):
        return [
            f"{repo.name}: {source} Config {name} has an empty pipe-delimited option"
        ]
    selected_value = (config.text or "").strip()
    if selected_value not in allowed_values:
        return [
            f"{repo.name}: {source} Config {name} selected value {selected_value!r} is not one of {allowed_values!r}"
        ]
    return []


def _manifest_declared_template_failures(
    repo: RepoConfig, source: str, root: Element
) -> list[str]:
    validation = _template_validation(repo, source)
    if not isinstance(validation, dict):
        return [f"{repo.name}: validation must be a mapping"]
    failures: list[str] = []

    required_text_fields = [
        str(field)
        for field in validation.get("required_text_fields", [])
        if str(field).strip()
    ]
    for field in required_text_fields:
        if not (root.findtext(field) or "").strip():
            failures.append(
                f"{repo.name}: {source} missing manifest-required non-empty <{field}>"
            )

    category_tokens = {
        token.strip()
        for token in (root.findtext("Category") or "").split()
        if token.strip()
    }
    allowed_category_tokens = {
        str(token)
        for token in validation.get("allowed_category_tokens", [])
        if str(token).strip()
    }
    if allowed_category_tokens:
        unknown = sorted(category_tokens - allowed_category_tokens)
        if unknown:
            failures.append(
                f"{repo.name}: {source} contains category tokens outside manifest allowlist: {', '.join(unknown)}"
            )

    exact_category_tokens = {
        str(token)
        for token in validation.get("exact_category_tokens", [])
        if str(token).strip()
    }
    if exact_category_tokens and category_tokens != exact_category_tokens:
        failures.append(
            f"{repo.name}: {source} category tokens must be exactly {', '.join(sorted(exact_category_tokens))}"
        )

    required_targets = {
        str(target)
        for target in validation.get("required_targets", [])
        if str(target).strip()
    }
    forbidden_targets = {
        str(target)
        for target in validation.get("forbidden_targets", [])
        if str(target).strip()
    }
    if not required_targets and not forbidden_targets:
        return failures

    targets = {
        config.attrib["Target"]
        for config in root.findall(".//Config")
        if config.attrib.get("Target")
    }
    missing = sorted(required_targets - targets)
    if missing:
        failures.append(
            f"{repo.name}: {source} missing manifest-required Config Target(s): {', '.join(missing)}"
        )
    forbidden = sorted(forbidden_targets & targets)
    if forbidden:
        failures.append(
            f"{repo.name}: {source} declares manifest-forbidden Config Target(s): {', '.join(forbidden)}"
        )
    return failures


def _template_validation(repo: RepoConfig, source: str) -> dict[str, object]:
    merged: dict[str, object] = {}
    top_level = repo.raw.get("validation", {})
    if isinstance(top_level, dict):
        merged.update(top_level)

    components = repo.raw.get("components", {})
    if not isinstance(components, dict):
        return merged
    for component in components.values():
        if not isinstance(component, dict):
            continue
        xml_paths = component.get("xml_paths", [])
        if isinstance(xml_paths, str):
            xml_sources = {xml_paths}
        elif isinstance(xml_paths, list):
            xml_sources = {str(item) for item in xml_paths}
        else:
            xml_sources = set()
        if source not in xml_sources:
            continue
        component_validation = component.get("validation", {})
        if isinstance(component_validation, dict):
            merged = _merge_validation(merged, component_validation)
    return merged


def _merge_validation(
    base: dict[str, object], override: dict[str, object]
) -> dict[str, object]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, list) and isinstance(merged.get(key), list):
            merged[key] = [*merged[key], *value]  # type: ignore[operator]
        else:
            merged[key] = value
    return merged


def _repository_registry_failures(
    repo: RepoConfig, source: str, root: Element
) -> list[str]:
    repository = (root.findtext("Repository") or "").strip()
    registry = (root.findtext("Registry") or "").strip()
    failures: list[str] = []

    repository_host = _repository_registry_host(repository)
    registry_host = urlparse(registry).netloc.lower()

    if repository_host == "ghcr.io":
        failures.append(
            f"{repo.name}: {source} <Repository> must use Docker Hub shorthand, got {repository}"
        )
    elif repository_host in {"docker.io", "registry-1.docker.io"}:
        failures.append(
            f"{repo.name}: {source} <Repository> must omit the Docker Hub registry prefix, got {repository}"
        )

    if registry_host == "ghcr.io":
        failures.append(
            f"{repo.name}: {source} <Registry> must point at Docker Hub, got {registry}"
        )

    image_name = _repository_image_name(repository)
    if image_name.startswith("jsonbored/"):
        expected_registry = f"https://hub.docker.com/r/{image_name}"
        if registry and registry != expected_registry:
            failures.append(
                f"{repo.name}: {source} <Registry> must be {expected_registry}, got {registry}"
            )

    return failures


def _repository_registry_host(repository: str) -> str:
    first_segment = repository.split("/", 1)[0].lower()
    if "." in first_segment or ":" in first_segment:
        return first_segment
    return ""


def _repository_image_name(repository: str) -> str:
    image = repository.split("@", 1)[0]
    tail = image.rsplit("/", 1)[-1]
    if ":" in tail:
        image = image.rsplit(":", 1)[0]
    return image


def _common_template_quality_failures(
    repo: RepoConfig, source: str, target: str, root: Element
) -> list[str]:
    failures: list[str] = []
    name = (root.findtext("Name") or "").strip()
    expected_name = Path(target).stem
    if name:
        if name != name.lower():
            failures.append(f"{repo.name}: {source} <Name> must be lowercase")
        if name != expected_name:
            failures.append(
                f"{repo.name}: {source} <Name> must match catalog target stem {expected_name}, got {name}"
            )

    for field in ["Project", "Support"]:
        value = (root.findtext(field) or "").strip()
        if value and not _is_http_url(value):
            failures.append(f"{repo.name}: {source} <{field}> must be an HTTP(S) URL")

    failures.extend(_category_failures(repo, source, root.findtext("Category") or ""))
    failures.extend(_donate_failures(repo, source, root))
    return failures


def _category_failures(repo: RepoConfig, source: str, category: str) -> list[str]:
    tokens = [token.strip() for token in category.split() if token.strip()]
    if not tokens:
        return [f"{repo.name}: {source} <Category> must contain at least one token"]

    failures: list[str] = []
    for token in tokens:
        if not UNRAID_CATEGORY_TOKEN.fullmatch(token):
            failures.append(
                f"{repo.name}: {source} <Category> token has invalid syntax: {token}"
            )
            continue
        root = token.rstrip(":").split(":", 1)[0]
        if root not in UNRAID_CATEGORY_ROOTS:
            failures.append(
                f"{repo.name}: {source} <Category> token has unknown root: {token}"
            )
    return failures


def _donate_failures(repo: RepoConfig, source: str, root: Element) -> list[str]:
    donate_text = root.find("DonateText")
    donate_link = root.find("DonateLink")
    failures: list[str] = []
    if donate_text is None:
        failures.append(f"{repo.name}: {source} missing <DonateText> tag")
    if donate_link is None:
        failures.append(f"{repo.name}: {source} missing <DonateLink> tag")
    if donate_text is None or donate_link is None:
        return failures

    text = (donate_text.text or "").strip()
    link = (donate_link.text or "").strip()
    if bool(text) != bool(link):
        failures.append(
            f"{repo.name}: {source} <DonateText> and <DonateLink> must be both blank or both populated"
        )
    if link and not _is_http_url(link):
        failures.append(f"{repo.name}: {source} <DonateLink> must be an HTTP(S) URL")
    return failures


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
