#!/usr/bin/env bash
set -euo pipefail

mock2_exam="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
mock2_python=""
if [[ -n "${MOCK2_PYTHON:-}" ]]; then
    mock2_python="$MOCK2_PYTHON"
elif [[ -x "$mock2_exam/../ta_v4_runtime/.venv/bin/python" ]]; then
    mock2_python="$mock2_exam/../ta_v4_runtime/.venv/bin/python"
elif [[ -x /home/project/ta_v4_runtime/.venv/bin/python ]]; then
    mock2_python="/home/project/ta_v4_runtime/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    mock2_python="$(command -v python3)"
else
    echo "找不到预装 Python。请等待实验环境准备完成，再重新打开终端。" >&2
    exit 1
fi

export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PIP_NO_INDEX=1
exec "$mock2_python" - "$mock2_exam" "${1:-start}" <<'PY'
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen


EXAM = Path(sys.argv[1]).resolve()
ACTION = sys.argv[2]
SUBMISSION = EXAM / "submission"
APP = SUBMISSION / "app.py"
METADATA = SUBMISSION / ".service.json"
LOG = EXAM / ".service.log"
BASE_URL = "http://127.0.0.1:8080"


def process_identity(pid):
    if type(pid) is not int or pid < 2:
        return None
    try:
        os.kill(pid, 0)
        proc = Path("/proc") / str(pid)
        if proc.is_dir():
            fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                return None
            args = (proc / "cmdline").read_bytes().decode().split("\0")
            return {"process_start_ticks": int(fields[19]), "arguments": args}
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart=", "-o", "args="],
            check=True, capture_output=True, text=True,
        )
        text = completed.stdout.strip()
        if not text:
            return None
        return {"process_started": text[:24], "arguments": text[24:].strip()}
    except (ProcessLookupError, OSError, ValueError, subprocess.CalledProcessError):
        return None


def matches_record(record):
    identity = process_identity(record.get("pid"))
    if identity is None:
        return False
    expected_key = "process_start_ticks" if "process_start_ticks" in identity else "process_started"
    return (
        record.get(expected_key) == identity[expected_key]
        and record.get("entrypoint") == str(APP)
        and str(APP) in identity["arguments"]
    )


def read_record():
    if METADATA.is_symlink():
        raise RuntimeError("服务进程记录不能是符号链接，请检查 " + str(METADATA))
    if not METADATA.exists():
        return None
    try:
        record = json.loads(METADATA.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("无法读取服务进程记录，请检查 " + str(METADATA)) from error
    if not isinstance(record, dict):
        raise RuntimeError("服务进程记录格式错误，请检查 " + str(METADATA))
    if process_identity(record.get("pid")) is None:
        METADATA.unlink()
        return None
    if not matches_record(record):
        raise RuntimeError("进程记录已失效，记录中的进程不属于本工程。请检查 " + str(METADATA))
    return record


def show(record, health):
    print(f"服务正在运行，PID {record['pid']}。")
    print(f"本机网页 {BASE_URL}/")
    print("云端网页请打开实验右侧“Web服务”，填写端口 8080。")
    print(f"运行日志 {LOG}")
    if health.get("ready") is not True:
        print("模型尚未就绪，请完成0101的配置后运行 bash tools/start.sh restart。")
    elif health.get("index_loaded") is not True:
        print("模型已就绪，索引尚未加载。完成0301建库和配置后重新启动服务。")
    else:
        print("模型与索引已就绪。")


def get_health():
    with urlopen(BASE_URL + "/health", timeout=1) as response:
        health = json.load(response)
    if not isinstance(health, dict) or not {"ready", "dimension", "index_loaded"} <= health.keys():
        raise ValueError("健康接口返回格式不符合本题约定")
    return health


def stop(record):
    if record is None:
        print("当前工程没有由启动工具管理的运行服务。")
        return
    pid = record["pid"]
    if os.getpgid(pid) != pid:
        raise RuntimeError("服务进程组与记录不符，请检查 PID " + str(pid))
    os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and matches_record(record):
        time.sleep(.1)
    if matches_record(record):
        os.killpg(pid, signal.SIGKILL)
    METADATA.unlink(missing_ok=True)
    print(f"已停止本工程服务，PID {pid}。")


def start():
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("0.0.0.0", 8080))
        except OSError as error:
            raise RuntimeError("8080端口正被其他进程使用。请先停止原服务，再运行启动工具。") from error
    if LOG.is_symlink():
        raise RuntimeError("服务日志不能是符号链接")
    with LOG.open("ab") as output:
        output.write(("\n--- service start " + time.strftime("%Y-%m-%d %H:%M:%S") + " ---\n").encode())
        output.flush()
        process = subprocess.Popen(
            [sys.executable, str(APP), "--resources", str(EXAM), "--host", "0.0.0.0", "--port", "8080"],
            cwd=EXAM, env=os.environ, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
        )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("服务启动失败。运行日志如下。\n" + LOG.read_text(errors="replace")[-2400:])
            try:
                health = get_health()
                if process.poll() is None:
                    break
            except (OSError, URLError, ValueError):
                pass
            time.sleep(.2)
        else:
            raise RuntimeError("60秒内未收到健康接口响应，请检查 " + str(LOG))
        identity = process_identity(process.pid)
        if identity is None or str(APP) not in identity["arguments"]:
            raise RuntimeError("无法确认本工程的服务进程身份")
        record = {
            "pid": process.pid,
            "base_url": BASE_URL,
            "started_at": time.time(),
            "entrypoint": str(APP),
            "resources": str(EXAM),
            "host": "0.0.0.0",
            "port": 8080,
            **{name: value for name, value in identity.items() if name != "arguments"},
        }
        fd, name = tempfile.mkstemp(prefix=".service-", dir=SUBMISSION)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False)
                handle.write("\n")
            temporary.replace(METADATA)
        finally:
            temporary.unlink(missing_ok=True)
        show(record, health)
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        raise


def main():
    if ACTION not in {"start", "status", "stop", "restart"}:
        raise RuntimeError("用法为 bash tools/start.sh [start|status|stop|restart]")
    if not SUBMISSION.is_dir():
        subprocess.run([sys.executable, str(EXAM / "tools/prepare.py")], check=True)
    if SUBMISSION.is_symlink() or not APP.is_file() or APP.is_symlink():
        raise RuntimeError("找不到普通作答工程 app.py，请检查 submission 目录")
    lock_path = SUBMISSION / ".service.lock"
    if lock_path.is_symlink():
        raise RuntimeError("服务启动锁不能是符号链接")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = read_record()
        if ACTION == "stop":
            stop(record)
        elif ACTION == "status":
            if record is None:
                print("当前工程服务未启动。运行 bash tools/start.sh 启动。")
            else:
                show(record, get_health())
        elif ACTION == "restart":
            stop(record)
            start()
        elif record is not None:
            show(record, get_health())
        else:
            start()


try:
    main()
except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
    print(str(error), file=sys.stderr)
    raise SystemExit(1)
PY
