from __future__ import annotations

import sys

from aio_fleet import github_app


def test_base64url_bytes_strips_padding() -> None:
    assert github_app._base64url_bytes(b"\xff\xee") == "_-4"  # nosec B101


def test_github_app_main_uses_fallback_token(monkeypatch, capsys) -> None:
    monkeypatch.setenv("AIO_FLEET_BOT_TOKEN", "fallback-token")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "github_app",
            "--fallback-env",
            "AIO_FLEET_BOT_TOKEN",
        ],
    )

    assert github_app.main() == 0  # nosec B101
    assert capsys.readouterr().out.strip() == "fallback-token"  # nosec B101


def test_github_app_main_prefers_app_credentials(monkeypatch, capsys) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_create_installation_token(
        app_id: str, installation_id: str, private_key: str
    ) -> str:
        calls.append((app_id, installation_id, private_key))
        return "app-token"

    monkeypatch.setenv("AIO_FLEET_APP_ID", "123")
    monkeypatch.setenv("AIO_FLEET_APP_INSTALLATION_ID", "456")
    monkeypatch.setenv("AIO_FLEET_APP_PRIVATE_KEY", "private-key")
    monkeypatch.setenv("AIO_FLEET_BOT_TOKEN", "fallback-token")
    monkeypatch.setattr(
        github_app, "create_installation_token", fake_create_installation_token
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "github_app",
            "--fallback-env",
            "AIO_FLEET_BOT_TOKEN",
        ],
    )

    assert github_app.main() == 0  # nosec B101
    assert capsys.readouterr().out.strip() == "app-token"  # nosec B101
    assert calls == [("123", "456", "private-key")]  # nosec B101
