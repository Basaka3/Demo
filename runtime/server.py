"""Provided HTTP and log skeleton; students need no web framework setup."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

from .engine import LibraryAssistant, ServiceNotReady


def _strict_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("JSON 对象含重复字段")
    return result


def _reject_json_constant(value):
    raise ValueError("非法 JSON 数值：" + value)


def make_server(resources: Path, submission: Path, host="127.0.0.1", port=8080, encoder_factory=None, policy=None):
    engine = LibraryAssistant(resources, submission, encoder_factory=encoder_factory, policy=policy)
    engine.start()
    submission = Path(submission).resolve()
    logs = submission / "logs/requests.jsonl"
    logs.parent.mkdir(parents=True, exist_ok=True)
    log_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "LibraryReturn/1"

        def log_message(self, *_args):
            pass

        def _send(self, status, payload, content_type="application/json; charset=utf-8"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                return
            with log_lock:
                with logs.open("a", encoding="utf-8") as handle:
                    record = dict(timestamp=time.time(), method=self.command, path=urlsplit(self.path).path,
                                  status=status, latency_ms=round((time.perf_counter()-self._start)*1000, 3))
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        def _body(self):
            if self.headers.get_content_type() != "application/json":
                raise ValueError("请求需要 Content-Type: application/json")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Content-Length 无效") from exc
            if not 1 <= length <= 16_384:
                raise ValueError("请求正文必须为1..16384字节")
            try:
                value = json.loads(
                    self.rfile.read(length).decode("utf-8"),
                    object_pairs_hook=_strict_object,
                    parse_constant=_reject_json_constant,
                )
            except (ValueError, UnicodeError) as exc:
                raise ValueError("请求正文必须是有效UTF-8 JSON") from exc
            if not isinstance(value, dict):
                raise ValueError("请求JSON必须是对象")
            return value

        def do_GET(self):
            self._start = time.perf_counter()
            path = urlsplit(self.path).path
            if path == "/health":
                self._send(200, engine.health())
            elif path == "/openapi.json":
                contract = engine.resources / "docs/openapi.json"
                if contract.is_file() and not contract.is_symlink():
                    self._send(200, contract.read_bytes())
                else:
                    self._send(404, {"error":"接口规范文件不存在"})
            elif path == "/":
                page = submission / "web/index.html"
                if page.is_file() and not page.is_symlink():
                    self._send(200, page.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._send(404, {"error":"网页文件不存在"})
            else:
                self._send(404, {"error":"接口不存在"})

        def do_POST(self):
            self._start = time.perf_counter()
            path = urlsplit(self.path).path
            if path not in {"/answer", "/embed", "/preview", "/cache/clear"}:
                self._send(404, {"error":"接口不存在"})
                return
            try:
                payload = self._body()
                if path == "/answer":
                    if "question" not in payload or set(payload) - {"question", "borrow_branch", "return_branch"}:
                        raise ValueError("/answer 需要 question，可附 borrow_branch 和 return_branch")
                    result = engine.answer(**payload)
                elif path == "/preview":
                    required = {"answer", "sources"}
                    if not required.issubset(payload) or set(payload) - (required | {"candidates"}):
                        raise ValueError("/preview 需要 answer、sources，可附候选 candidates")
                    if "candidates" in payload and not isinstance(payload["candidates"], list):
                        raise ValueError("candidates 必须是数组")
                    result = engine.preview(**payload)
                elif path == "/embed":
                    if set(payload) != {"texts"}:
                        raise ValueError("/embed 仅接受 texts 字段")
                    result = engine.embed(payload["texts"])
                else:
                    if payload:
                        raise ValueError("/cache/clear 需要空对象 {}")
                    result = engine.clear_cache()
                self._send(200, result)
            except ServiceNotReady as exc:
                self._send(503, {"error":str(exc)})
            except ValueError as exc:
                self._send(400, {"error":str(exc)})
            except Exception:
                self._send(500, {"error":"服务处理失败，请检查运行日志和模型资源"})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.engine = engine
    return server


def run(resources: Path, submission: Path, policy=None):
    parser = argparse.ArgumentParser(description="图书馆跨馆归还咨询服务")
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--resources", type=Path, default=resources)
    arguments = parser.parse_args()
    server = make_server(arguments.resources, submission, arguments.host, arguments.port, policy=policy)
    print(f"图书馆跨馆归还咨询服务已启动：http://{arguments.host}:{arguments.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
