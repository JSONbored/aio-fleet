from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

SUCCESS_STATES = {"success", "succeeded", "ok", "up", "passed"}
FAILURE_STATES = {"failure", "failed", "error", "down", "timed_out", "cancelled"}
WARNING_STATES = {"warning", "blocked", "missing", "updates", "attention"}
WEBHOOK_EVENT_ALLOWLIST = {
    "recovery",
    "registry-audit",
    "release-readiness",
    "upstream-monitor",
    "upstream-update",
}


@dataclass(frozen=True)
class AlertPayload:
    event: str
    status: str
    severity: str
    summary: str
    dedupe_key: str
    repo: str = ""
    component: str = ""
    details_url: str = ""
    annotations: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_status(status: str) -> str:
    normalized = status.strip().lower().replace(" ", "_") or "success"
    if normalized in SUCCESS_STATES:
        return "success"
    if normalized in FAILURE_STATES:
        return "failure"
    if normalized in WARNING_STATES:
        return "warning"
    return normalized


def alert_payload(
    *,
    event: str,
    status: str,
    summary: str,
    repo: str = "",
    component: str = "",
    details_url: str = "",
    dedupe_key: str = "",
    annotations: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> AlertPayload:
    normalized = normalize_status(status)
    severity = (
        "critical"
        if normalized == "failure"
        else "warning" if normalized == "warning" else "info"
    )
    key_parts = [event, repo or "fleet", component or "all"]
    key = dedupe_key or ":".join(key_parts)
    return AlertPayload(
        event=event,
        status=normalized,
        severity=severity,
        summary=summary or f"{event}: {normalized}",
        dedupe_key=key,
        repo=repo,
        component=component,
        details_url=details_url,
        annotations=annotations or [],
        details=details or {},
    )


def payload_from_report(
    *,
    event: str,
    report: dict[str, Any],
    status: str = "auto",
    summary: str = "",
    details_url: str = "",
    dedupe_key: str = "",
) -> AlertPayload:
    derived_status, derived_summary, annotations, details = summarize_report(
        event, report
    )
    return alert_payload(
        event=event,
        status=derived_status if status == "auto" else status,
        summary=summary or derived_summary,
        details_url=details_url,
        dedupe_key=dedupe_key,
        annotations=annotations,
        details=details,
    )


def summarize_report(
    event: str, report: dict[str, Any]
) -> tuple[str, str, list[str], dict[str, Any]]:
    repos = report.get("repos", [])
    if not isinstance(repos, list):
        repos = []
    if event == "upstream-monitor":
        errors = [
            item for item in repos if isinstance(item, dict) and item.get("error")
        ]
        updates: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []
        for item in repos:
            if not isinstance(item, dict):
                continue
            for result in item.get("results", []):
                if isinstance(result, dict) and result.get("updates_available"):
                    updates.append({"repo": item.get("repo", ""), **result})
            for action in item.get("actions", []):
                if isinstance(action, dict) and action.get("action") not in {
                    "skipped",
                    "",
                }:
                    actions.append(action)
        if errors:
            summary = f"Upstream monitor failed for {len(errors)} repo(s)"
            annotations = [
                f"{item.get('repo', 'unknown')}: {item.get('error', 'unknown error')}"
                for item in errors[:10]
            ]
            return "failure", summary, annotations, {"errors": errors}
        if updates or actions:
            summary = f"Upstream updates available for {len(updates)} component(s)"
            annotations = [
                "{repo}:{component} {current_version} -> {latest_version}".format(
                    repo=item.get("repo", ""),
                    component=item.get("component", "aio"),
                    current_version=item.get("current_version", ""),
                    latest_version=item.get("latest_version", ""),
                )
                for item in updates[:10]
            ]
            return (
                "warning",
                summary,
                annotations,
                {"updates": updates, "actions": actions},
            )
        return "success", "Upstream monitor found no updates", [], {}

    if event == "registry-audit":
        failures: list[str] = []
        for item in repos:
            if not isinstance(item, dict):
                continue
            repo = str(item.get("repo", "unknown"))
            component = str(item.get("component", "aio"))
            for failure in item.get("failures", []):
                failures.append(f"{repo}:{component}: {failure}")
        if failures:
            return (
                "failure",
                f"Registry audit found {len(failures)} missing or failed tag(s)",
                failures[:10],
                {"failures": failures},
            )
        return "success", "Registry audit passed", [], {}

    return "success", f"{event}: success", [], {}


def should_send_webhook(payload: AlertPayload, *, force: bool = False) -> bool:
    if force:
        return True
    if payload.event == "recovery":
        return True
    if payload.status in {"failure", "warning"}:
        return True
    return payload.event in WEBHOOK_EVENT_ALLOWLIST and payload.status != "success"


def send_kuma_push(push_url: str, payload: AlertPayload) -> str:
    parsed = urllib.parse.urlsplit(push_url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query.update(
        {
            "status": "down" if payload.status == "failure" else "up",
            "msg": payload.summary[:180],
        }
    )
    url = urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(query),
            parsed.fragment,
        )
    )
    with urllib.request.urlopen(url, timeout=20) as response:  # nosec B310
        response.read()
    return "sent"


def send_webhook(
    webhook_url: str, payload: AlertPayload, *, webhook_format: str = "json"
) -> None:
    if webhook_format == "text":
        body = _text_body(payload).encode()
        headers = {"Content-Type": "text/plain; charset=utf-8"}
    else:
        body = json.dumps(payload.as_dict(), sort_keys=True).encode()
        headers = {"Content-Type": "application/json"}
    request = urllib.request.Request(
        webhook_url,
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:  # nosec B310
        response.read()


def emit_alert(
    payload: AlertPayload,
    *,
    kuma_url: str = "",
    webhook_url: str = "",
    webhook_format: str = "json",
    force_webhook: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    deliveries: dict[str, Any] = {
        "payload": payload.as_dict(),
        "kuma": "skipped",
        "webhook": "skipped",
    }
    if kuma_url:
        deliveries["kuma"] = (
            "would-send" if dry_run else send_kuma_push(kuma_url, payload)
        )
    if webhook_url and should_send_webhook(payload, force=force_webhook):
        if dry_run:
            deliveries["webhook"] = "would-send"
        else:
            send_webhook(webhook_url, payload, webhook_format=webhook_format)
            deliveries["webhook"] = "sent"
    return deliveries


def _text_body(payload: AlertPayload) -> str:
    lines = [
        payload.summary,
        f"status={payload.status}",
        f"dedupe={payload.dedupe_key}",
    ]
    if payload.details_url:
        lines.append(payload.details_url)
    lines.extend(f"- {annotation}" for annotation in payload.annotations)
    return "\n".join(lines) + "\n"
