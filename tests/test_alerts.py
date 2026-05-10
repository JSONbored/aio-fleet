from __future__ import annotations

import json
import urllib.parse
import urllib.request

from aio_fleet import alerts


def test_kuma_push_encodes_status_and_preserves_existing_query(monkeypatch) -> None:
    seen: list[str] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return b"OK"

    def fake_urlopen(url: str, timeout: int):
        seen.append(url)
        assert timeout == 20  # nosec B101
        return Response()

    monkeypatch.setattr(alerts.urllib.request, "urlopen", fake_urlopen)
    payload = alerts.alert_payload(
        event="control-plane",
        status="failure",
        summary="control-plane failed",
    )

    alerts.send_kuma_push("https://kuma.example/api/push/token?ping=12", payload)

    parsed = urllib.parse.urlsplit(seen[0])
    query = dict(urllib.parse.parse_qsl(parsed.query))
    assert query["ping"] == "12"  # nosec B101
    assert query["status"] == "down"  # nosec B101
    assert query["msg"] == "control-plane failed"  # nosec B101


def test_webhook_sends_failure_with_dedupe_key(monkeypatch) -> None:
    seen: list[urllib.request.Request] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return b"OK"

    def fake_urlopen(request: urllib.request.Request, timeout: int):
        seen.append(request)
        assert timeout == 20  # nosec B101
        return Response()

    monkeypatch.setattr(alerts.urllib.request, "urlopen", fake_urlopen)
    payload = alerts.alert_payload(
        event="registry-audit",
        status="failure",
        summary="missing tags",
        dedupe_key="registry-audit:fleet",
    )

    result = alerts.emit_alert(payload, webhook_url="https://hooks.example/fleet")

    assert result["webhook"] == "sent"  # nosec B101
    body = json.loads(seen[0].data.decode())  # type: ignore[union-attr]
    assert body["dedupe_key"] == "registry-audit:fleet"  # nosec B101
    assert body["status"] == "failure"  # nosec B101
    assert seen[0].headers["User-agent"] == "aio-fleet-alerts/1.0"  # nosec B101


def test_discord_webhook_uses_discord_payload(monkeypatch) -> None:
    seen: list[urllib.request.Request] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return b"OK"

    def fake_urlopen(request: urllib.request.Request, timeout: int):
        seen.append(request)
        assert timeout == 20  # nosec B101
        return Response()

    monkeypatch.setattr(alerts.urllib.request, "urlopen", fake_urlopen)
    payload = alerts.alert_payload(
        event="upstream-monitor",
        status="warning",
        summary="upstream updates available",
        dedupe_key="upstream-monitor:fleet:all",
        annotations=["sure-aio:aio 0.7.0 -> 0.7.1"],
    )

    alerts.send_webhook("https://discord.com/api/webhooks/example/token", payload)

    body = json.loads(seen[0].data.decode())  # type: ignore[union-attr]
    assert body["allowed_mentions"]["parse"] == []  # nosec B101
    assert body["content"].startswith("aio-fleet: upstream updates")  # nosec B101
    assert seen[0].headers["User-agent"] == "aio-fleet-alerts/1.0"  # nosec B101
    assert body["embeds"][0]["fields"][2]["value"] == (  # nosec B101
        "upstream-monitor:fleet:all"
    )


def test_success_webhook_is_skipped_unless_recovery_or_forced() -> None:
    payload = alerts.alert_payload(
        event="control-plane",
        status="success",
        summary="control-plane recovered",
    )
    recovery = alerts.alert_payload(
        event="recovery",
        status="success",
        summary="control-plane recovered",
    )

    assert (
        alerts.emit_alert(payload, webhook_url="https://hooks", dry_run=True)["webhook"]
        == "skipped"
    )  # nosec B101
    assert (
        alerts.emit_alert(recovery, webhook_url="https://hooks", dry_run=True)[
            "webhook"
        ]
        == "would-send"
    )  # nosec B101
    assert (
        alerts.emit_alert(
            payload,
            webhook_url="https://hooks",
            force_webhook=True,
            dry_run=True,
        )["webhook"]
        == "would-send"
    )  # nosec B101


def test_upstream_report_alert_detects_updates_and_actions() -> None:
    payload = alerts.payload_from_report(
        event="upstream-monitor",
        status="auto",
        report={
            "repos": [
                {
                    "repo": "sure-aio",
                    "results": [
                        {
                            "component": "aio",
                            "current_version": "0.7.0",
                            "latest_version": "0.7.1",
                            "updates_available": True,
                        }
                    ],
                    "actions": [
                        {
                            "action": "upserted-pr",
                            "url": "https://github.com/JSONbored/sure-aio/pull/80",
                        }
                    ],
                }
            ]
        },
    )

    assert payload.status == "warning"  # nosec B101
    assert payload.dedupe_key == "upstream-monitor:fleet:all"  # nosec B101
    assert payload.annotations == ["sure-aio:aio 0.7.0 -> 0.7.1"]  # nosec B101
    assert payload.details["actions"][0]["action"] == "upserted-pr"  # nosec B101


def test_registry_report_alert_detects_missing_tags() -> None:
    payload = alerts.payload_from_report(
        event="registry-audit",
        status="auto",
        report={
            "repos": [
                {
                    "repo": "sure-aio",
                    "component": "aio",
                    "failures": ["jsonbored/sure-aio:latest: tag not found"],
                }
            ]
        },
    )

    assert payload.status == "failure"  # nosec B101
    assert "1 missing or failed tag" in payload.summary  # nosec B101
