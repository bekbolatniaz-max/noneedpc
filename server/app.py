"""FastAPI-приложение: HTTP по contracts/openapi.yaml + источники ввода + раздача PWA."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from server import __version__
from server.core.config import ROOT
from server.core.context import AppContext
from server.core.protocol import PROTOCOL_VERSION
from server.services.base import ServiceError
from server.services.pairing import PairedDevice, PairingError

log = logging.getLogger(__name__)

API = "/api/v1"
GB = 1024**3

_STATUS_CODES = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
    405: "bad_request", 409: "conflict", 422: "bad_request", 429: "rate_limited",
}


def _error(status: int, message: str, code: str | None = None) -> JSONResponse:
    code = code or _STATUS_CODES.get(status, "internal")
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


class _Strict(BaseModel):
    """Как в OpenAPI: без лишних полей, без приведения типов ("70" ≠ 70) и без явного null."""

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="before")
    @classmethod
    def _no_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            nulls = [k for k, v in data.items() if v is None]
            if nulls:
                raise ValueError(f"null не допускается: {', '.join(nulls)}")
        return data


class PairRequest(_Strict):
    code: str = Field(pattern=r"^[0-9]{6}$")
    device_name: str = Field(default="", max_length=64)


class ConsoleModeRequest(_Strict):
    state: Literal["on", "off"]


class VolumeRequest(_Strict):
    volume: int | None = Field(default=None, ge=0, le=100)
    muted: bool | None = None


class PowerRequest(_Strict):
    action: Literal["sleep", "restart", "shutdown"]
    confirm: Literal[True]


def create_app(ctx: AppContext) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        deadman = asyncio.create_task(ctx.router.run_deadman())
        for source in ctx.inputs:
            await source.start(ctx)
        try:
            yield
        finally:
            for source in ctx.inputs:
                await source.stop()
            aclose = getattr(ctx.console_mode, "aclose", None)
            if aclose is not None:
                await aclose()
            deadman.cancel()
            ctx.router.backend.shutdown()

    app = FastAPI(title="Nitro Console", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        return _error(400, f"{where}: {first.get('msg', 'неверный запрос')}")

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return _error(exc.status, exc.message, exc.code)

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        log.exception("необработанная ошибка: %r", exc)
        return _error(500, "внутренняя ошибка сервера", "internal")

    def auth(authorization: Annotated[str | None, Header()] = None) -> PairedDevice:
        token = None
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:].strip()
        device = ctx.pairing.verify(token)
        if device is None:
            raise StarletteHTTPException(401, "нужно сопряжение")
        return device

    Auth = Annotated[PairedDevice, Depends(auth)]
    AppId = Annotated[int, PathParam(ge=1)]

    @app.get(f"{API}/info")
    def info() -> dict[str, Any]:
        cfg = ctx.config
        backends = [
            {"id": cls.id, "title": cls.title, "available": cls.available(), "analog": cls.analog}
            for cls in ctx.registry.all("output").values()
        ]
        return {
            "name": cfg["instance"]["name"],
            "server_version": __version__,
            "protocol_version": PROTOCOL_VERSION,
            "mock": ctx.mock,
            "capabilities": {
                "output_backends": backends,
                "active_backend": ctx.router.backend.id,
                "max_slots": ctx.router.max_slots,
                "deadman_ms": cfg["input"]["deadman_ms"],
                "send_hz": cfg["input"]["send_hz"],
                "rumble": bool(ctx.features.get("rumble")),
            },
            "features": ctx.features,
        }

    @app.post(f"{API}/pair")
    def pair(body: PairRequest) -> Any:
        try:
            token, device_id = ctx.pairing.pair(body.code, body.device_name)
        except PairingError as e:
            status = {"rate_limited": 429, "internal": 500}.get(e.code, 403)
            return _error(status, e.message, e.code)
        log.info("сопряжено устройство %s", device_id)  # токен не логируем
        return {"token": token, "device_id": device_id}

    @app.get(f"{API}/pair/code")
    def pair_code(_: Auth) -> dict[str, Any]:
        code, expires_in = ctx.pairing.code_info()
        return {"code": code, "expires_in_s": expires_in}

    @app.get(f"{API}/games")
    def games(_: Auth) -> dict[str, Any]:
        return {"games": [
            {"appid": g.appid, "name": g.name, "library_id": g.library_id,
             "size_bytes": g.size_bytes, "available": g.available, "has_cover": g.has_cover}
            for g in ctx.library.games()
        ]}

    @app.get(f"{API}/games/{{appid}}/cover")
    def cover(appid: AppId, _: Auth) -> Response:
        c = ctx.library.cover(appid)
        if c is None:
            return _error(404, "обложки нет")
        return Response(c.data, media_type=c.media_type,
                        headers={"Cache-Control": "private, max-age=86400"})

    @app.post(f"{API}/games/{{appid}}/launch", status_code=202)
    async def launch(appid: AppId, _: Auth) -> dict[str, Any]:
        await ctx.launcher.launch(appid)
        return {"appid": appid, "status": "launching"}

    def _console() -> dict[str, Any]:
        s = ctx.console_mode.status()
        out: dict[str, Any] = {"state": s.state, "display_profile": s.display_profile}
        if s.message:
            out["message"] = s.message
        return out

    @app.get(f"{API}/console-mode")
    def console_get(_: Auth) -> dict[str, Any]:
        return _console()

    @app.post(f"{API}/console-mode", status_code=202)
    async def console_set(body: ConsoleModeRequest, _: Auth) -> dict[str, Any]:
        await ctx.console_mode.set(body.state == "on")
        return _console()

    @app.get(f"{API}/system")
    def system_get(_: Auth) -> dict[str, Any]:
        volume, muted = ctx.system.volume()
        return {"volume": volume, "muted": muted}

    @app.post(f"{API}/system/volume")
    async def system_volume(body: VolumeRequest, _: Auth) -> dict[str, Any]:
        volume, muted = await ctx.system.set_volume(body.volume, body.muted)
        return {"volume": volume, "muted": muted}

    @app.post(f"{API}/system/power", status_code=202)
    async def system_power(body: PowerRequest, device: Auth) -> dict[str, Any]:
        log.warning("питание: %s по запросу %s", body.action, device.device_id)
        await ctx.system.power(body.action)
        return {"action": body.action, "status": "accepted"}

    @app.get(f"{API}/devices")
    def devices(_: Auth) -> dict[str, Any]:
        return {"devices": [
            {"id": s.device.id, "kind": s.device.kind, "name": s.device.name, "slot": s.index,
             "mode": s.device.mode, "capabilities": list(s.device.capabilities)}
            for s in ctx.router.slots()
        ]}

    @app.get(f"{API}/storage")
    def storage(_: Auth) -> dict[str, Any]:
        low = int(ctx.config["storage"]["low_space_gb"] * GB)
        return {"volumes": [
            {"id": v.id, "kind": v.kind, "label": v.label, "mount": v.mount,
             "total_bytes": v.total_bytes, "free_bytes": v.free_bytes, "available": v.available,
             "low_space": v.available and v.free_bytes < low,
             "libraries": [
                 {"id": lib.id, "path": lib.path, "game_count": lib.game_count,
                  "size_bytes": lib.size_bytes}
                 for lib in v.libraries
             ]}
            for v in ctx.storage.volumes()
        ]}

    for source in ctx.inputs:
        source.mount(app, ctx)

    pwa_dir = Path(ctx.config["server"].get("pwa_dir") or ROOT / "pwa")
    if ctx.config["server"].get("serve_pwa") and pwa_dir.is_dir():
        app.mount("/", StaticFiles(directory=pwa_dir, html=True), name="pwa")

    return app
