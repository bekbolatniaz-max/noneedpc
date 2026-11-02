"""Мок-сервер обязан отвечать строго по contracts/openapi.yaml."""

import re
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server.tests.schema import assert_valid, load_openapi

SPEC = load_openapi()


def check(resp: Any, path: str, method: str) -> Any:
    """Проверить ответ по схеме из OpenAPI для (path, method, status)."""
    op = SPEC["paths"][path][method]
    status = str(resp.status_code)
    assert status in op["responses"], f"{method.upper()} {path}: статус {status} не описан в контракте"
    spec_resp = op["responses"][status]
    if "$ref" in spec_resp:
        spec_resp = SPEC["components"]["responses"][spec_resp["$ref"].rsplit("/", 1)[1]]
    content = spec_resp.get("content", {})
    if "application/json" in content:
        body = resp.json()
        assert_valid(body, content["application/json"]["schema"], SPEC)
        return body
    ctype = resp.headers["content-type"].split(";")[0]
    assert ctype in content, f"{method.upper()} {path}: content-type {ctype} не описан"
    return resp.content


def test_every_contract_route_is_implemented(client: TestClient) -> None:
    implemented = {
        (route.path.removeprefix("/api/v1"), m.lower())
        for route in client.app.routes
        if getattr(route, "methods", None) and route.path.startswith("/api/v1")
        for m in route.methods
        if m != "HEAD"
    }
    declared = {(p, m) for p, ops in SPEC["paths"].items() for m in ops}
    assert declared == implemented


def test_info_without_auth(client: TestClient) -> None:
    body = check(client.get("/api/v1/info"), "/info", "get")
    assert body["protocol_version"] == 1 and body["mock"] is True
    assert body["capabilities"]["active_backend"] == "mock"
    assert body["capabilities"]["deadman_ms"] == 250


def test_pair_flow(client: TestClient) -> None:
    check(client.post("/api/v1/pair", json={"code": "000000"}), "/pair", "post")
    assert client.post("/api/v1/pair", json={"code": "000000"}).status_code == 403
    assert client.post("/api/v1/pair", json={"code": "12ab"}).status_code == 400
    assert client.post("/api/v1/pair", json={"code": "123456", "x": 1}).status_code == 400
    body = check(client.post("/api/v1/pair", json={"code": "123456"}), "/pair", "post")
    assert len(body["token"]) >= 32


@pytest.mark.parametrize(
    ("method", "path", "spec_path"),
    [
        ("get", "/games", "/games"),
        ("get", "/games/1001/cover", "/games/{appid}/cover"),
        ("post", "/games/1001/launch", "/games/{appid}/launch"),
        ("get", "/console-mode", "/console-mode"),
        ("get", "/system", "/system"),
        ("get", "/devices", "/devices"),
        ("get", "/storage", "/storage"),
    ],
)
def test_auth_required(client: TestClient, method: str, path: str, spec_path: str) -> None:
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "nope"}):
        r = client.request(method, f"/api/v1{path}", headers=headers)
        assert r.status_code == 401
        body = check(r, spec_path, method)
        assert body["error"]["code"] == "unauthorized"


def test_token_not_accepted_in_query(client: TestClient, token: str) -> None:
    assert client.get(f"/api/v1/games?token={token}").status_code == 401


def test_games_and_covers(client: TestClient, auth: dict[str, str]) -> None:
    games = check(client.get("/api/v1/games", headers=auth), "/games", "get")["games"]
    assert games
    for g in games:
        r = client.get(f"/api/v1/games/{g['appid']}/cover", headers=auth)
        assert r.status_code == (200 if g["has_cover"] else 404)
        data = check(r, "/games/{appid}/cover", "get")
        if g["has_cover"]:
            assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_launch(client: TestClient, auth: dict[str, str]) -> None:
    body = check(client.post("/api/v1/games/1001/launch", headers=auth), "/games/{appid}/launch", "post")
    assert body == {"appid": 1001, "status": "launching"}
    r = client.post("/api/v1/games/999999/launch", headers=auth)
    assert r.status_code == 404
    check(r, "/games/{appid}/launch", "post")
    r = client.post("/api/v1/games/1004/launch", headers=auth)  # диск отключён
    assert r.status_code == 409
    check(r, "/games/{appid}/launch", "post")


def test_console_mode_switches(client: TestClient, auth: dict[str, str]) -> None:
    body = check(client.get("/api/v1/console-mode", headers=auth), "/console-mode", "get")
    assert body["state"] == "off"
    r = client.post("/api/v1/console-mode", json={"state": "on"}, headers=auth)
    assert r.status_code == 202
    assert check(r, "/console-mode", "post")["state"] == "switching"
    r = client.post("/api/v1/console-mode", json={"state": "off"}, headers=auth)
    assert r.status_code == 409
    check(r, "/console-mode", "post")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        body = client.get("/api/v1/console-mode", headers=auth).json()
        if body["state"] == "on":
            break
        time.sleep(0.05)
    assert body == {"state": "on", "display_profile": "mock-tv"}
    assert client.post("/api/v1/console-mode", json={"state": "maybe"}, headers=auth).status_code == 400


def test_system(client: TestClient, auth: dict[str, str]) -> None:
    check(client.get("/api/v1/system", headers=auth), "/system", "get")
    body = check(client.post("/api/v1/system/volume", json={"volume": 70}, headers=auth),
                 "/system/volume", "post")
    assert body == {"volume": 70, "muted": False}
    body = check(client.post("/api/v1/system/volume", json={"muted": True}, headers=auth),
                 "/system/volume", "post")
    assert body == {"volume": 70, "muted": True}
    assert client.post("/api/v1/system/volume", json={"volume": 101}, headers=auth).status_code == 400


@pytest.mark.parametrize("body", [{"volume": None}, {"muted": None}, {"volume": "70"}, {"muted": 1}])
def test_volume_rejects_null_and_wrong_types(client: TestClient, auth: dict[str, str], body: Any) -> None:
    r = client.post("/api/v1/system/volume", json=body, headers=auth)
    assert r.status_code == 400
    check(r, "/system/volume", "post")


@pytest.mark.parametrize("appid", ["0", "-1", "abc"])
def test_bad_appid_is_400_per_contract(client: TestClient, auth: dict[str, str], appid: str) -> None:
    r = client.get(f"/api/v1/games/{appid}/cover", headers=auth)
    assert r.status_code == 400
    check(r, "/games/{appid}/cover", "get")
    r = client.post(f"/api/v1/games/{appid}/launch", headers=auth)
    assert r.status_code == 400
    check(r, "/games/{appid}/launch", "post")


def test_power_requires_confirm(client: TestClient, ctx: Any, auth: dict[str, str]) -> None:
    bad_bodies = (
        {"action": "shutdown"},
        {"action": "shutdown", "confirm": False},
        {"action": "x", "confirm": True},
    )
    for body in bad_bodies:
        r = client.post("/api/v1/system/power", json=body, headers=auth)
        assert r.status_code == 400
        check(r, "/system/power", "post")
    assert ctx.system.power_requests == []
    r = client.post("/api/v1/system/power", json={"action": "sleep", "confirm": True}, headers=auth)
    assert r.status_code == 202
    check(r, "/system/power", "post")
    assert ctx.system.power_requests == ["sleep"]


def test_devices_and_storage(client: TestClient, auth: dict[str, str]) -> None:
    assert check(client.get("/api/v1/devices", headers=auth), "/devices", "get") == {"devices": []}
    vols = check(client.get("/api/v1/storage", headers=auth), "/storage", "get")["volumes"]
    by_id = {v["id"]: v for v in vols}
    assert by_id["vol-e"]["available"] is False and by_id["vol-e"]["low_space"] is False
    lib_ids = {lib["id"] for v in vols for lib in v["libraries"]}
    games = client.get("/api/v1/games", headers=auth).json()["games"]
    assert {g["library_id"] for g in games} <= lib_ids


def test_unknown_route_uses_error_format(client: TestClient) -> None:
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    assert re.fullmatch(r"[a-z_]+", r.json()["error"]["code"])
