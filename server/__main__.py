"""python -m server [--host 0.0.0.0] [--port 8765] — настоящий сервер Nitro Console."""

import argparse
import logging

import uvicorn

from server.app import create_app
from server.build import build_context
from server.net import lan_ip


def main() -> None:
    p = argparse.ArgumentParser(description="Nitro Console сервер")
    p.add_argument("--host", default=None, help="по умолчанию из config (127.0.0.1); телефон: 0.0.0.0")
    p.add_argument("--port", type=int, default=None)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    url = {"value": ""}

    def show_code(code: str) -> None:
        print(f"\n  >>> Код сопряжения: {code}   (на телефоне: {url['value']}#pair={code})\n", flush=True)

    ctx, warnings = build_context(on_new_code=show_code)
    host = args.host or ctx.config["server"]["host"]
    port = args.port or ctx.config["server"]["port"]
    url["value"] = f"http://{lan_ip() if host == '0.0.0.0' else host}:{port}/"

    print(f"\n  Nitro Console  {url['value']}")
    print(f"  Вывод: {ctx.router.backend.id}   Игр в Steam: {len(ctx.library.games())}")
    for w in warnings:
        print(f"  ! {w}")
    if host == "127.0.0.1":
        print("  ! слушаю только этот компьютер; для телефона: --host 0.0.0.0")
    ctx.pairing.current_code()
    uvicorn.run(create_app(ctx), host=host, port=port, log_level="info", ws="websockets")


if __name__ == "__main__":
    main()
