#!/usr/bin/env python3
"""Check the public answer files for missing or malformed results."""
import json
from pathlib import Path

from package import ANSWER_NAMES

RESOURCES = Path(__file__).resolve().parents[1]


def inspect_answers(directory):
    for filename in sorted(ANSWER_NAMES):
        path = directory / filename
        status = "未保存"
        if path.is_file() and path.stat().st_size:
            status = "格式有误"
            if path.stat().st_size <= 65536 and not path.is_symlink():
                try:
                    content = path.read_text(encoding="utf-8").strip()
                    valid = len(content.splitlines()) == 2 if filename == "0201.txt" else isinstance(json.loads(content), dict)
                    if valid:
                        status = "格式通过"
                except (OSError, ValueError):
                    pass
        yield filename, status


if __name__ == "__main__":
    for name, description in inspect_answers(RESOURCES / "submission/answers"):
        print(f"{name}  {description}")
