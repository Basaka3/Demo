"""运行参赛工程，可用 --resources 指定公共素材目录。"""
import argparse
import os
from pathlib import Path
import sys


def main():
    submission = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="图书馆跨馆归还咨询服务")
    parser.add_argument("--resources", type=Path)
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    explicit = args.resources or os.environ.get("TA_RESOURCES")
    candidates = [Path(explicit)] if explicit else [
        submission.parent,
        submission.parent.parent / "1-题目素材/exam",
    ]
    resources = next((path.resolve() for path in candidates if (path / "runtime/engine.py").is_file()), None)
    if resources is None:
        parser.error("找不到公共素材，请用 --resources 指定 exam 目录")

    sys.path.insert(0, str(resources))
    from runtime.server import make_server
    from src.policy import resolve

    server = make_server(resources, submission, host=args.host, port=args.port, policy=resolve)
    print(f"图书馆跨馆归还咨询服务已启动：http://{args.host}:{args.port}", flush=True)
    print(f"服务进程 PID：{os.getpid()}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
