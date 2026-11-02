"""python -m server.mock [--host 0.0.0.0] [--port 8765] [--pair-code 123456]"""

import argparse
import logging
import re
import socket

import uvicorn

from server.app import create_app
from server.mock.build import build_mock_context


def _lan_ip() -> str:
    # Без отправки пакетов: UDP connect только выбирает исходящий интерфейс.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def main() -> None:
    p = argparse.ArgumentParser(description="Nitro Console мок-сервер (без драйвера и Steam)")
    p.add_argument("--host", default=None, help="по умолчанию из config (127.0.0.1)")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--pair-code", default=None,
                   help="постоянный многоразовый код (только мок, для тестов PWA)")
    p.add_argument("--pwa-dir", default=None, help="папка PWA (по умолчанию <repo>/pwa)")
    args = p.parse_args()
    if args.pair_code is not None and not re.fullmatch(r"[0-9]{6}", args.pair_code):
        p.error("--pair-code: ровно 6 цифр")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    overrides = {"server": {"pwa_dir": args.pwa_dir}} if args.pwa_dir else None
    def show_code(code: str) -> None:
        print(f"  Код сопряжения: {code} (одноразовый, 5 минут)", flush=True)

    ctx = build_mock_context(pair_code=args.pair_code, on_new_code=show_code, overrides=overrides)
    host = args.host or ctx.config["server"]["host"]
    port = args.port or ctx.config["server"]["port"]
    shown = _lan_ip() if host == "0.0.0.0" else host

    print(f"\n  Nitro Console MOCK  http://{shown}:{port}/")
    print("  API: /api/v1/info   WS: /ws")
    if args.pair_code:
        print(f"  Код сопряжения: {args.pair_code} (ПОСТОЯННЫЙ: только мок, без TTL и лимита попыток)")
    else:
        ctx.pairing.current_code()  # напечатает через show_code; новые коды — по мере смены
    print()
    uvicorn.run(create_app(ctx), host=host, port=port, log_level="info", ws="websockets")


if __name__ == "__main__":
    main()
