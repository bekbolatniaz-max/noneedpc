"""Парсер текстового KeyValues (VDF/ACF) Steam: только чтение, без eval."""

from typing import Any


class VdfError(ValueError):
    pass


def _tokens(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "/" and text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif c in "{}":
            yield c
            i += 1
        elif c == '"':
            i += 1
            out = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    out.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
                    i += 2
                else:
                    out.append(text[i])
                    i += 1
            if i >= n:
                raise VdfError("незакрытая кавычка")
            i += 1
            yield ("s", "".join(out))
        else:
            start = i
            while i < n and text[i] not in ' \t\r\n{}"':
                i += 1
            yield ("s", text[start:i])


def loads(text: str) -> dict[str, Any]:
    """Ключи — регистрозависимые строки, значения — строки или вложенные словари."""
    root: dict[str, Any] = {}
    stack = [root]
    key: str | None = None
    for tok in _tokens(text.lstrip("﻿")):
        if tok == "{":
            if key is None:
                raise VdfError("блок без ключа")
            child: dict[str, Any] = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif tok == "}":
            if len(stack) == 1 or key is not None:
                raise VdfError("лишняя }")
            stack.pop()
        elif key is None:
            key = tok[1]
        else:
            stack[-1][key] = tok[1]
            key = None
    if len(stack) != 1 or key is not None:
        raise VdfError("неожиданный конец файла")
    return root
