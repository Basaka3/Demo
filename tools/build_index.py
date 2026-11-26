#!/usr/bin/env python3
"""Build a real Paddle knowledge index from the current config and resources."""
import argparse
import json
from pathlib import Path
import sys

RESOURCES = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RESOURCES))
from runtime.engine import build_index


def main():
    parser = argparse.ArgumentParser(description="构建馆藏规则索引；不修改基础模型")
    parser.add_argument("--submission", type=Path, default=RESOURCES / "submission")
    parser.add_argument("--resources", type=Path, default=RESOURCES)
    arguments = parser.parse_args()
    try:
        print(json.dumps(build_index(arguments.resources, arguments.submission), ensure_ascii=False, indent=2))
    except Exception as exc:
        parser.exit(1, "索引构建失败：" + str(exc) + "\n")


if __name__ == "__main__":
    main()
