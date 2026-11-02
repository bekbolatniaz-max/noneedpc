"""Проверка на секреты перед коммитом: python .verify/secret_scan.py [--all]

По умолчанию — файлы в индексе git (git add). --all — все отслеживаемые файлы.
Код выхода 1, если что-то найдено. Значения не печатаются, только файл и строка.
"""

import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "github token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "openai/anthropic key": re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b"),
    "aws key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "password assignment": re.compile(
        r"""(?i)\b(password|passwd|secret|api_key|apikey)\b\s*[:=]\s*["'][^"'\s]{6,}["']"""
    ),
    "bearer literal": re.compile(r"(?i)authorization:\s*bearer\s+[A-Za-z0-9._~+/-]{24,}"),
}
FORBIDDEN_FILES = re.compile(r"(^|/)(config/local\.yaml|\.env(\..*)?|.*\.(pem|key))$")
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".ico", ".woff", ".woff2"}


def files(all_files: bool) -> list[str]:
    cmd = ["git", "ls-files"] if all_files else ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
    return [f for f in subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.splitlines() if f]


def main() -> int:
    found = 0
    for name in files("--all" in sys.argv):
        if FORBIDDEN_FILES.search(name):
            print(f"{name}: файл не должен быть в git")
            found += 1
            continue
        path = Path(name)
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file() or name.endswith("secret_scan.py"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, rx in PATTERNS.items():
                if rx.search(line):
                    print(f"{name}:{lineno}: похоже на секрет ({label})")
                    found += 1
    print("секретов не найдено" if not found else f"найдено подозрений: {found}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
