#!/usr/bin/env python3
"""Create a blank working copy on first launch."""
import argparse
from pathlib import Path
import shutil

RESOURCES = Path(__file__).resolve().parents[1]


def prepare(template: Path, destination: Path) -> bool:
    if destination.is_symlink():
        raise ValueError("作答目录不能是链接")
    if destination.exists():
        if not destination.is_dir():
            raise ValueError("作答路径已存在且不是目录")
        return False
    shutil.copytree(template, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="首次建立作答工程，保留已有作答")
    parser.add_argument("--submission", type=Path, default=RESOURCES / "submission")
    args = parser.parse_args()
    print("起步工程已建立。" if prepare(RESOURCES / "template", args.submission) else "使用现有作答工程。")
