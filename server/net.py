"""Сетевые мелочи для точек входа."""

import socket


def lan_ip() -> str:
    """IP в локальной сети. Без отправки пакетов: UDP connect только выбирает исходящий интерфейс."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"
