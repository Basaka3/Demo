#!/usr/bin/env python3
"""Write the project manifest and the final submission archive."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
import tempfile
import zipfile

RESOURCES = Path(__file__).resolve().parents[1]
IDENTITY = {"version": "library-v1", "entrypoint": "app.py", "port": 8080}
ROOT_FILES = {"app.py", "config.json", "README.md", "manifest.json"}
REQUIRED = ROOT_FILES - {"manifest.json"}
DIRECTORIES = {"prompts", "src", "web", "artifacts", "answers"}
SUFFIXES = {".py", ".json", ".txt", ".md", ".html", ".css", ".js", ".npz", ".svg", ".png", ".jpg"}
SKIP = {"runtime", "models", "data", "logs", "cache", "caches", "__pycache__", "venv", "node_modules"}
ANSWER_NAMES = {f"{module:02d}0{question}.txt" for module in range(1, 6) for question in (1, 2)}
MAX_SIZE = 20 * 1024 * 1024
MAX_FILES = 200


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def select_payload(root: Path) -> dict[str, bytes]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("作答目录须为实际目录")
    payload = {}
    total = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        name = relative.as_posix()
        if path.is_symlink():
            raise ValueError(f"交卷不得包含链接：{name}")
        if relative.parts[0] not in DIRECTORIES and name not in ROOT_FILES:
            continue
        if any(part.startswith(".") or part in SKIP for part in relative.parts):
            continue
        if name == "manifest.json" or path.is_dir():
            continue
        if not path.is_file() or path.suffix not in SUFFIXES:
            raise ValueError(f"不支持的交卷文件：{name}")
        if relative.parts[0] == "answers":
            if len(relative.parts) != 2 or relative.name not in ANSWER_NAMES:
                raise ValueError(f"结果文件路径有误：{name}")
            if path.stat().st_size > 65536:
                raise ValueError(f"结果文件超过 64 KiB：{name}")
        if path.stat().st_size + total > MAX_SIZE or len(payload) >= MAX_FILES - 1:
            raise ValueError("交卷最多 200 个文件及 20 MiB")
        data = path.read_bytes()
        if relative.parts[0] == "answers":
            data.decode("utf-8")
        payload[name] = data
        total += len(data)
    if not REQUIRED <= payload.keys():
        raise ValueError("缺少作答起步文件：" + "、".join(sorted(REQUIRED - payload.keys())))
    return payload


def project_identity(root: Path) -> dict:
    payload = select_payload(root)
    hashes = {name: _digest(data) for name, data in payload.items() if not name.startswith("answers/")}
    canonical = json.dumps(hashes, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {**IDENTITY, "payload_sha256": _digest(canonical)}


def package_submission(root: Path, output: Path) -> dict:
    if output.resolve().is_relative_to(root.resolve()):
        raise ValueError("交卷 ZIP 必须位于作答目录之外")
    payload = select_payload(root)
    manifest = {**IDENTITY, "files": {name: _digest(data) for name, data in payload.items()}}
    manifest_data = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if sum(map(len, payload.values())) + len(manifest_data) > MAX_SIZE:
        raise ValueError("交卷解压后超过 20 MiB")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".zip", delete=False) as handle:
        staged = Path(handle.name)
    try:
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in {**payload, "manifest.json": manifest_data}.items():
                archive.writestr("submission/" + name, data)
        if staged.stat().st_size > MAX_SIZE:
            raise ValueError("交卷 ZIP 超过 20 MiB")
        (root / "manifest.json").write_bytes(manifest_data)
        staged.replace(output)
    finally:
        staged.unlink(missing_ok=True)
    return {"output": str(output), "files": len(payload) + 1, "bytes": output.stat().st_size, "sha256": _digest(output.read_bytes())}



def main(argv=None):
    parser = argparse.ArgumentParser(description="采集当前结果，生成清单及最终提交包")
    parser.add_argument("--submission", type=Path, default=RESOURCES / "submission")
    parser.add_argument("--output", type=Path, default=RESOURCES / "submission.zip")
    parser.add_argument("--resources", type=Path, default=RESOURCES)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--pid", type=int, help="兼容手动启动的服务 PID")
    parser.add_argument("--browser", help="可选的 Chromium 可执行文件路径")
    parser.add_argument("--no-refresh", action="store_true", help="直接打包当前文件，供内部复现")
    args = parser.parse_args(argv)
    try:
        report = None
        if not args.no_refresh:
            from results import collect_answers
            report = collect_answers(args.resources, args.submission, args.base_url, args.pid, args.browser)
        result = package_submission(args.submission, args.output)
        if report is not None:
            result["collection"] = report
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError, TypeError) as exc:
        print("打包未完成：" + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
