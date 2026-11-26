#!/usr/bin/env python3
"""Measure the local answer service from a separate process."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import statistics
import sys
import threading
import time
from urllib.request import Request, urlopen

RESOURCES = Path(__file__).resolve().parents[1]
REQUEST_TIMEOUT = 12
WORKERS = 4
REQUESTS = 24
EXPECTED = {
    "P01": ("ALLOW", "W", [], ["R01"]),
    "P03": ("DENY", "E", [], ["R02"]),
    "P05": ("NEED_INFO", None, ["return_branch"], []),
}
FACT_FIELDS = ("decision", "return_to", "missing_fields", "sources")


def request(base_url, path, payload=None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(base_url.rstrip("/") + path, data=body, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
        return json.load(response)


def _process_tree_rss(process_id):
    participants = {}
    for status in Path("/proc").glob("[0-9]*/status"):
        try:
            values = dict(line.split(":", 1) for line in status.read_text().splitlines() if ":" in line)
            participants[int(status.parent.name)] = (
                int(values["PPid"]),
                int(values["VmRSS"].split()[0]) * 1024,
            )
        except (OSError, KeyError, ValueError):
            continue
    if process_id not in participants:
        return None
    tree = {process_id}
    while True:
        children = {pid for pid, (parent, _) in participants.items() if parent in tree}
        if children <= tree:
            return sum(participants[pid][1] for pid in tree)
        tree |= children


def benchmark(base_url, process_id, public_cases):
    query_ids = public_cases["benchmark"]
    queries = [public_cases["answers"][key] for key in query_ids]
    if not queries:
        raise ValueError("测速样例为空")
    health = request(base_url, "/health")
    if not health.get("ready") or not health.get("index_loaded"):
        raise ValueError("请先启动模型及加载索引")
    if health.get("cache_size") != 32:
        raise ValueError("请先完成本题的 32 条缓存配置")
    peak = _process_tree_rss(process_id)
    if peak is None:
        raise ValueError("无法采样服务进程，请确认 PID 及 Linux 环境")

    baseline = []
    for _ in range(2):
        baseline = [request(base_url, "/answer", item) for item in queries]

    def sample(position, cold=False):
        case = position % len(queries)
        if cold:
            request(base_url, "/cache/clear", {})
        start = time.perf_counter()
        try:
            result = request(base_url, "/answer", queries[case])
            facts = tuple(result[field] for field in FACT_FIELDS)
            answer = result.get("answer")
            success = (facts == EXPECTED[query_ids[case]] and isinstance(answer, str)
                       and answer == baseline[case].get("answer"))
        except (KeyError, OSError, TypeError, ValueError):
            success = False
        return (time.perf_counter() - start) * 1000, success

    stop = threading.Event()

    def monitor():
        nonlocal peak
        while not stop.is_set():
            measured = _process_tree_rss(process_id)
            if measured is not None:
                peak = max(peak, measured)
            stop.wait(0.05)

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    try:
        serial = [sample(position, cold=True) for position in range(REQUESTS)]
        for item in queries:
            request(base_url, "/answer", item)
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            parallel = list(pool.map(sample, range(REQUESTS)))
    finally:
        stop.set()
        watcher.join()

    latency = sorted(elapsed for elapsed, _ in serial)
    return {
        "cache_size": health["cache_size"],
        "mean_ms": round(statistics.mean(latency), 3),
        "p95_ms": round(latency[math.ceil(0.95 * len(latency)) - 1], 3),
        "peak_rss_bytes": peak,
        "concurrent_success_rate": sum(success for _, success in parallel) / REQUESTS,
        "concurrent_requests": REQUESTS,
        "concurrent_workers": WORKERS,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="实际测量串行时延、热缓存并发和进程树内存")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--pid", type=int, required=True)
    args = parser.parse_args()
    cases = json.loads((RESOURCES / "data/public_cases.json").read_text(encoding="utf-8"))
    try:
        print(json.dumps(benchmark(args.base_url, args.pid, cases), ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError) as exc:
        print(f"测速未完成：{exc}", file=sys.stderr)
        sys.exit(1)
