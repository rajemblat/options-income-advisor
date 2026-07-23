from __future__ import annotations

import httpx
import pytest

from options_advisor.alerts import notifier


@pytest.fixture(autouse=True)
def _clear_telegram_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


def test_notify_without_telegram_env_never_calls_httpx(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append((a, k)))
    notifier.notify("AAPL", "cash_secured_put", 80, "texto de la alerta")
    assert calls == []


def test_notify_sends_message_to_configured_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    captured = {}

    def _post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr(httpx, "post", _post)
    notifier.notify("AAPL", "cash_secured_put", 80, "texto de la alerta")

    assert captured["url"] == "https://api.telegram.org/botfake-token/sendMessage"
    assert captured["json"] == {"chat_id": "12345", "text": "texto de la alerta"}


def test_notify_never_raises_when_telegram_request_fails(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    def _boom(*args, **kwargs):
        raise httpx.ConnectError("no network", request=httpx.Request("POST", "https://api.telegram.org/x"))

    monkeypatch.setattr(httpx, "post", _boom)
    notifier.notify("AAPL", "cash_secured_put", 80, "texto de la alerta")  # no debe lanzar


def test_notify_never_raises_on_http_error_status(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    def _post(url, json, timeout):
        request = httpx.Request("POST", url)
        return httpx.Response(400, json={"ok": False, "description": "bad chat id"}, request=request)

    monkeypatch.setattr(httpx, "post", _post)
    notifier.notify("AAPL", "cash_secured_put", 80, "texto de la alerta")  # no debe lanzar


def test_notify_truncates_message_over_telegram_limit(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    captured = {}

    def _post(url, json, timeout):
        captured["text"] = json["text"]
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr(httpx, "post", _post)
    long_text = "x" * 5000
    notifier.notify("AAPL", "cash_secured_put", 80, long_text)

    assert len(captured["text"]) <= notifier._TELEGRAM_MAX_MESSAGE_LENGTH
    assert captured["text"].endswith("[…continúa en el dashboard]")


def test_notify_only_bot_token_set_does_not_call_httpx(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    calls = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append((a, k)))
    notifier.notify("AAPL", "cash_secured_put", 80, "texto")
    assert calls == []
