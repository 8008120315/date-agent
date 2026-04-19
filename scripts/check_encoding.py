#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


UTF8_BOM = b"\xef\xbb\xbf"


def check_utf8(path: Path) -> list[str]:
    issues: list[str] = []
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"read failed: {exc}"]

    if data.startswith(UTF8_BOM):
        issues.append("contains UTF-8 BOM")

    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        issues.append(f"not UTF-8 (decode error at byte {exc.start})")

    return issues


def main(argv: list[str]) -> int:
    targets = [Path(p) for p in argv[1:]]
    failures: list[tuple[Path, list[str]]] = []

    for path in targets:
        if not path.exists() or path.is_dir():
            continue
        issues = check_utf8(path)
        if issues:
            failures.append((path, issues))

    if not failures:
        return 0

    print("Encoding check failed. Please use UTF-8 without BOM:")
    for path, issues in failures:
        print(f"- {path}")
        for issue in issues:
            print(f"  - {issue}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

