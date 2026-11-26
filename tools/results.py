#!/usr/bin/env python3
"""Read one public result from the current service or project."""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from benchmark import benchmark, request
from package import project_identity

RESOURCES = Path(__file__).resolve().parents[1]
QUESTIONS = [f"{module:02d}0{question}" for module in range(1, 6) for question in (1, 2)]
ANSWER_FIELDS = ("decision", "return_to", "missing_fields", "sources", "answer")


def select(result, keys):
    return {key: result[key] for key in keys}


def observed_result(question, resources, submission, base_url, process_id=None, browser_path=None):
    public = json.loads((resources / "data/public_cases.json").read_text(encoding="utf-8"))

    def call(path, payload=None):
        return request(base_url, path, payload)

    def answers(ids, fields=None):
        collected = []
        for identifier in ids:
            payload = public["answers"][identifier]
            response = call("/answer", payload)
            collected.append({"input": payload, "result": select(response, fields) if fields else response})
        return collected

    if question == "0101":
        result = select(call("/health"), ("ready", "dimension", "index_loaded"))
        if result["ready"] is not True:
            raise ValueError("模型尚未就绪")
        return result
    if question == "0102":
        texts = public["embed_texts"]
        return {"texts": texts, **select(call("/embed", {"texts": texts}), ("dimension", "vectors"))}
    if question == "0201":
        return call("/preview", public["preview_format"])["answer"]
    if question == "0202":
        return call("/preview", public["preview_limits"])
    if question == "0301":
        metadata = json.loads((submission / "artifacts/index.meta.json").read_text(encoding="utf-8"))
        return select(metadata, ("rows", "dimension", "dtype", "doc_ids", "index_sha256"))
    if question == "0302":
        return {"results": answers(public["answer_run"])}
    if question == "0401":
        return {"reuse_index": call("/health")["reuse_index"], "results": answers(public["reuse_run"])}
    if question == "0402":
        process_id = service_pid(submission, base_url, process_id)
        return benchmark(base_url, process_id, public)
    if question == "0501":
        return observe_webpage(base_url, public["answers"][public["web_case"]], browser_path)
    if question == "0502":
        return project_identity(submission)
    raise ValueError("未知题号")



def process_start_ticks(process_id):
    stat = Path(f"/proc/{process_id}/stat").read_text()
    fields = stat[stat.rfind(")") + 2:].split()
    return int(fields[19])


def service_pid(submission, base_url, explicit=None):
    if explicit is not None:
        if type(explicit) is not int or explicit <= 0:
            raise ValueError("服务 PID 必须为正整数")
        process_start_ticks(explicit)
        return explicit
    path = submission / ".service.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        process_id = record["pid"]
        if type(process_id) is not int or process_id <= 0:
            raise ValueError("服务 PID 无效")
        if record["base_url"].rstrip("/") != base_url.rstrip("/"):
            raise ValueError("服务记录的访问地址不同，请重新启动服务或提供 --pid")
        if type(record["process_start_ticks"]) is not int:
            raise ValueError("服务启动记录不完整")
        if process_start_ticks(process_id) != record["process_start_ticks"]:
            raise ValueError("服务进程已更换，请运行 bash tools/start.sh status")
        return process_id
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("找不到当前服务，请先运行 bash tools/start.sh，或提供 --pid") from exc


def observe_webpage(base_url, query, browser_path=None):
    """实际填写并提交网页，核对同源响应、页面答复和来源后采集。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ValueError("0501 需要预装的 Playwright 和 Chromium，请使用题目运行环境") from exc

    base = base_url.rstrip("/")
    try:
        with sync_playwright() as playwright:
            arguments = ["--disable-dev-shm-usage"]
            if os.geteuid() == 0:
                arguments.append("--no-sandbox")
            browser = playwright.chromium.launch(executable_path=browser_path, args=arguments)
            try:
                page = browser.new_page()
                page.route(
                    "**/*",
                    lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort(),
                )
                page.goto(base + "/", wait_until="domcontentloaded", timeout=12000)
                for name, identifier in (("borrow_branch", "#borrow-branch"), ("return_branch", "#return-branch")):
                    control = page.locator(identifier)
                    value = query.get(name) or ""
                    if control.evaluate("(node) => node.tagName") == "SELECT":
                        control.select_option(value, timeout=12000)
                    else:
                        control.fill(value, timeout=12000)
                page.locator("#question").fill(query["question"], timeout=12000)
                with page.expect_response(
                    lambda result: result.url == base + "/answer" and result.request.method == "POST",
                    timeout=12000,
                ) as observed:
                    page.locator("#send").click(timeout=12000)
                response = observed.value
                sent = response.request.post_data_json
                if response.status != 200 or not isinstance(sent, dict) or any(
                    sent.get(field) != query.get(field)
                    for field in ("question", "borrow_branch", "return_branch")
                ):
                    raise ValueError("0501 网页提交的请求与公开用例不一致，或服务未成功响应")
                result = select(response.json(), ANSWER_FIELDS)
                page.wait_for_function(
                    """expected => {
                        const answer = document.querySelector('#answer');
                        return answer && answer.getAttribute('data-decision') === expected.decision
                            && answer.innerText.trim() === expected.answer.trim();
                    }""",
                    arg=result, timeout=12000,
                )
                source_text = page.locator("#sources").inner_text(timeout=12000)
                visible_ids = re.findall(r"(?<![A-Z0-9])R[0-9]{2}(?![0-9])", source_text)
                if visible_ids != result["sources"]:
                    raise ValueError("0501 网页显示的资料编号与响应不一致")
                return {"input": query, "result": result}
            finally:
                browser.close()
    except Exception as exc:
        raise ValueError("0501 网页采集未完成：" + str(exc)) from exc


def result_text(question, value):
    if question == "0201":
        if not isinstance(value, str) or not value.strip():
            raise ValueError("0201 未取得答复正文")
        text = value
    else:
        if not isinstance(value, dict):
            raise ValueError("未取得完整 JSON 结果")
        text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    if "\x00" in text or not text.strip():
        raise ValueError("采集结果为空或含非法字符")
    return text if text.endswith("\n") else text + "\n"


def save_result(submission, question, value):
    if question not in QUESTIONS:
        raise ValueError("未知题号")
    content = result_text(question, value).encode("utf-8")
    if len(content) > 65536:
        raise ValueError("采集结果超过 64 KiB")
    if not submission.is_dir() or submission.is_symlink():
        raise ValueError("作答目录须为实际目录")
    folder = submission / "answers"
    if folder.is_symlink():
        raise ValueError("答案目录不能为链接")
    folder.mkdir(exist_ok=True)
    target = folder / (question + ".txt")
    if target.is_symlink() or target.exists() and not target.is_file():
        raise ValueError("答案文件不能为链接或目录")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix="." + question + "-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def collect_answers(resources, submission, base_url, process_id=None, browser_path=None):
    report = {}
    for question in QUESTIONS:
        path = submission / "answers" / (question + ".txt")
        try:
            value = observed_result(question, resources, submission, base_url, process_id, browser_path)
            save_result(submission, question, value)
            report[question] = {"status": "saved", "path": str(path)}
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            preserved = path.is_file() and not path.is_symlink() and path.stat().st_size > 0
            report[question] = {
                "status": "preserved" if preserved else "unavailable", "reason": str(exc),
            }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="采集实际结果，自动保存对应答案文件并输出正文")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--question", choices=QUESTIONS)
    group.add_argument("--all", action="store_true", help="依次采集各题，失败时保留该题已有结果")
    parser.add_argument("--pid", type=int, help="手动启动服务时可显式提供 PID")
    parser.add_argument("--resources", type=Path, default=RESOURCES)
    parser.add_argument("--submission", type=Path, default=RESOURCES / "submission")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--browser", help="可选的 Chromium 可执行文件路径")
    parser.add_argument("--no-save", action="store_true", help="仅输出单题结果，供内部复现")
    args = parser.parse_args(argv)
    if args.all:
        if args.no_save:
            parser.error("--no-save 仅用于单题")
        report = collect_answers(args.resources, args.submission, args.base_url, args.pid, args.browser)
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    try:
        value = observed_result(
            args.question, args.resources, args.submission, args.base_url, args.pid, args.browser,
        )
        text = result_text(args.question, value)
        if not args.no_save:
            path = save_result(args.submission, args.question, value)
            print("已保存 " + str(path), file=sys.stderr)
        print(text, end="")
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        print(f"采集未完成，已保留原答案：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
