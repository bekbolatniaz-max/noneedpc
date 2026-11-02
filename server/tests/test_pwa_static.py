"""PWA раздаётся сервером с того же origin; статические проверки безопасности фронтенда."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.mock.build import build_mock_context

PWA = Path(__file__).resolve().parents[2] / "pwa"


def test_pwa_served_same_origin_api_still_wins() -> None:
    ctx = build_mock_context(pair_code="123456", overrides={"server": {"serve_pwa": True}})
    with TestClient(create_app(ctx)) as c:
        r = c.get("/")
        assert r.status_code == 200 and "Nitro Pad" in r.text
        js = c.get("/js/protocol.js")
        assert js.status_code == 200 and "javascript" in js.headers["content-type"]
        assert c.get("/api/v1/info").json()["protocol_version"] == 1  # API не перекрыт статикой


def test_no_unsafe_dom_or_external_resources() -> None:
    for f in [*PWA.glob("js/*.js"), PWA / "index.html"]:
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(", text), f.name
        assert not re.search(r"""(src|href)=["']https?://""", text), f"{f.name}: внешний ресурс"


def test_token_never_in_url_or_console() -> None:
    for f in PWA.glob("js/*.js"):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"[?&]token=", text), f.name
        assert "console.log" not in text, f.name
