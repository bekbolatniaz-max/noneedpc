from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.core.context import AppContext
from server.mock.build import build_mock_context

PAIR_CODE = "123456"


@pytest.fixture
def ctx() -> AppContext:
    # serve_pwa выключен: тесты не зависят от наличия pwa/
    return build_mock_context(
        pair_code=PAIR_CODE, overrides={"server": {"serve_pwa": False}, "input": {"hello_timeout_s": 1}}
    )


@pytest.fixture
def client(ctx: AppContext) -> Iterator[TestClient]:
    with TestClient(create_app(ctx)) as c:
        yield c


@pytest.fixture
def token(client: TestClient) -> str:
    r = client.post("/api/v1/pair", json={"code": PAIR_CODE, "device_name": "pytest"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture
def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
