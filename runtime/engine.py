"""公共检索、配置、索引、渲染和运行支持。馆藏规则判定留在参赛工程。"""
from collections import OrderedDict
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import string
import tempfile
import threading
import zipfile

import numpy as np

from .encoder import DIMENSION, PaddleEncoder


CONFIG_KEYS = {"model_enabled", "device", "max_length", "prefix", "top_k", "min_score",
               "max_answer_chars", "index_path", "reuse_index", "cache_size"}
RULE_ID = re.compile(r"R[0-9]{2}")
BRANCHES = {"E", "W", "S"}


class ServiceNotReady(RuntimeError):
    """The HTTP adapter maps this exception to 503."""


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _config(submission):
    config = _read_json(submission / "config.json")
    if not isinstance(config, dict) or set(config) != CONFIG_KEYS:
        raise ValueError("config.json 的字段与资源协议不一致")
    for key in ("model_enabled", "reuse_index"):
        if type(config[key]) is not bool:
            raise ValueError(key + " 必须是布尔值")
    if config["device"] != "cpu":
        raise ValueError("device 必须为 cpu")
    for key, low, high in (("max_length", 16, 512), ("top_k", 1, 12),
                           ("max_answer_chars", 1, 2000), ("cache_size", 0, 1024)):
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(key + f" 必须为 {low}..{high} 的整数")
    if type(config["min_score"]) not in (int, float) or not math.isfinite(config["min_score"]) or not -1 <= config["min_score"] <= 1:
        raise ValueError("min_score 必须是 -1..1 的有限数值")
    if not isinstance(config["prefix"], str) or len(config["prefix"]) > 64:
        raise ValueError("prefix 必须是不超过64字符的文本")
    _index_path(submission, config["index_path"])
    return config


def _index_path(submission, relative, default=False):
    if not isinstance(relative, str):
        raise ValueError("index_path 必须是相对路径字符串")
    if not relative:
        if not default:
            return None
        relative = "artifacts/index.npz"
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".npz" or "\\" in relative:
        raise ValueError("index_path 必须是工程目录内的 .npz 相对路径")
    target = submission / path
    for parent in (target, *target.parents):
        if parent == submission:
            break
        if parent.is_symlink():
            raise ValueError("索引路径不允许软链接")
    if not target.resolve().is_relative_to(submission.resolve()):
        raise ValueError("索引路径越界")
    return target


def _prompt(submission):
    prompt = (submission / "prompts/answer.txt").read_text(encoding="utf-8")
    if not 1 <= len(prompt) <= 2000:
        raise ValueError("回答模板必须为1..2000字符")
    fields = []
    for _, field, spec, conversion in string.Formatter().parse(prompt):
        if field is not None:
            if field not in {"prefix", "answer", "sources"} or spec or conversion:
                raise ValueError("模板只允许 {prefix}、{answer}、{sources} 简单占位符")
            fields.append(field)
    if set(fields) != {"prefix", "answer", "sources"}:
        raise ValueError("模板必须使用 prefix、answer、sources 三个占位符")
    return prompt



def _rules(resources):
    path = resources / "data/rules.jsonl"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 1_000_000:
        raise ValueError("规则资料缺失或大小无效")
    rules = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rule = json.loads(line)
        if not isinstance(rule, dict) or set(rule) != {"id", "title", "text"}:
            raise ValueError("规则资料字段错误")
        if not isinstance(rule["id"], str) or not RULE_ID.fullmatch(rule["id"]):
            raise ValueError("规则编号必须符合 Rxx 格式")
        if not all(isinstance(rule[key], str) and 1 <= len(rule[key].strip()) <= 2000
                   for key in ("title", "text")):
            raise ValueError("规则资料标题或正文错误")
        rules.append(rule)
    if not 1 <= len(rules) <= 100 or len({rule["id"] for rule in rules}) != len(rules):
        raise ValueError("规则资料数量或编号错误")
    return rules


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()



def _identity(resources, config, rules):
    weights = resources / "models/course_semantic_model/model_state.pdparams"
    if not weights.is_file() or weights.is_symlink():
        raise FileNotFoundError("本地模型资源缺失或不是普通文件")
    return dict(rows=len(rules), dimension=DIMENSION, dtype="float32",
                doc_ids=[rule["id"] for rule in rules],
                rules_sha256=_sha(resources / "data/rules.jsonl"),
                model_sha256=_sha(weights), device=config["device"],
                max_length=config["max_length"])


def _search_text(rule):
    return rule["title"] + "：" + rule["text"]


def _vectors(encoder, texts):
    vectors = np.asarray(encoder.encode(texts))
    if vectors.dtype != np.float32 or vectors.shape != (len(texts), DIMENSION) or not np.isfinite(vectors).all():
        raise ValueError("编码结果必须是有限的 float32(N,312) 数组")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if (norms < 1e-12).any():
        raise ValueError("编码结果不能是零向量")
    return (vectors / norms).astype(np.float32, copy=False)


def _encoder(resources, config, factory):
    encoder = (factory or PaddleEncoder)(resources / "models/course_semantic_model",
                                        max_length=config["max_length"], device=config["device"])
    encoder.load()
    return encoder


def build_index(resources: Path, submission: Path, encoder_factory=None) -> dict:
    resources, submission = Path(resources).resolve(), Path(submission).resolve()
    config = _config(submission)
    if not config["model_enabled"]:
        raise ValueError("请先启用 model_enabled，再构建真实模型索引")
    rules = _rules(resources)
    identity = _identity(resources, config, rules)
    encoder = _encoder(resources, config, encoder_factory)
    vectors = _vectors(encoder, [_search_text(rule) for rule in rules])
    target = _index_path(submission, config["index_path"], default=True)
    metadata = target.with_suffix(".meta.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    if metadata.is_symlink():
        raise ValueError("索引metadata不允许使用符号链接")
    files = []
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            temporary_index = Path(handle.name)
            files.append(temporary_index)
            np.savez(handle, vectors=vectors)
        meta = dict(identity, index_sha256=_sha(temporary_index))
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as handle:
            temporary_meta = Path(handle.name)
            files.append(temporary_meta)
            json.dump(meta, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary_index.replace(target)
        temporary_meta.replace(metadata)
    finally:
        for file in files:
            file.unlink(missing_ok=True)
    return meta



def _load_index(target, identity):
    metadata = target.with_suffix(".meta.json")
    if metadata.is_symlink() or not metadata.is_file() or metadata.stat().st_size > 100_000:
        raise ValueError("索引metadata缺失或无效；请重建索引")
    meta = _read_json(metadata)
    if not isinstance(meta, dict) or any(meta.get(key) != value for key, value in identity.items()):
        raise ValueError("索引已过期或资源不匹配；请重建索引")
    if target.stat().st_size > 2_000_000 or meta.get("index_sha256") != _sha(target):
        raise ValueError("索引文件大小或哈希校验失败")
    try:
        with zipfile.ZipFile(target) as archive:
            members = archive.infolist()
            if [member.filename for member in members] != ["vectors.npy"] or sum(member.file_size for member in members) > 2_000_000:
                raise ValueError("索引压缩包内容无效")
        with np.load(target, allow_pickle=False) as data:
            vectors = data["vectors"]
        if vectors.dtype != np.float32 or vectors.shape != (identity["rows"], DIMENSION) or not np.isfinite(vectors).all():
            raise ValueError("索引数组类型或维度无效")
        if not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-4, rtol=0):
            raise ValueError("索引向量必须归一化")
        return vectors.copy()
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ValueError("索引无法读取；请重建索引") from exc


def validate_question(question):
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 200:
        raise ValueError("文本必须是去除首尾空白后1至200字符的字符串")
    return question.strip()


def _validate_branch(value, name):
    if value is not None and (not isinstance(value, str) or value not in BRANCHES):
        raise ValueError(name + " 必须为 E、W、S 或 null")


class LibraryAssistant:
    def __init__(self, resources, submission, encoder_factory=None, policy=None):
        self.resources = Path(resources).resolve()
        self.submission = Path(submission).resolve()
        self.config = _config(self.submission)
        self.prompt = _prompt(self.submission).rstrip("\r\n")
        self.rules = _rules(self.resources)
        self._rule_ids = {row["id"] for row in self.rules}
        self._rule_texts = [_search_text(rule) for rule in self.rules]
        self._factory = encoder_factory
        self._policy = policy
        self._encoder = None
        self._index = None
        self._cache = OrderedDict()
        self._lock = threading.RLock()
        self._ready = False
        self._started = False
        self.model_loads = self.query_encodes = self.doc_encodes = self.cache_hits = 0

    def start(self):
        with self._lock:
            if self._started:
                return
            if self.config["model_enabled"]:
                target = _index_path(self.submission, self.config["index_path"])
                if target is not None and target.is_file():
                    identity = _identity(self.resources, self.config, self.rules)
                    self._index = _load_index(target, identity)
                self._encoder = _encoder(self.resources, self.config, self._factory)
                self.model_loads += 1
                self._ready = True
            self._started = True

    def health(self):
        with self._lock:
            return {
                "ready": self._ready,
                "dimension": DIMENSION,
                "index_loaded": self._index is not None,
                "model_loads": self.model_loads,
                "query_encodes": self.query_encodes,
                "doc_encodes": self.doc_encodes,
                "cache_hits": self.cache_hits,
                "cache_entries": len(self._cache),
                "cache_size": self.config["cache_size"],
                "reuse_index": self.config["reuse_index"],
            }

    def clear_cache(self):
        with self._lock:
            self._cache.clear()
            return {"cache_entries": 0}

    def _render(self, content, sources):
        values = {"prefix": self.config["prefix"], "answer": content, "sources": "、".join(sources) or "无"}
        formatted = self.prompt.format(**values)
        limit = self.config["max_answer_chars"]
        if len(formatted) <= limit:
            return formatted
        values["answer"] = ""
        remaining = limit - len(self.prompt.format(**values))
        if remaining < 0:
            return formatted[:limit]
        occurrences = sum(field == "answer" for _, field, _, _ in string.Formatter().parse(self.prompt))
        values["answer"] = content[:remaining // occurrences]
        return self.prompt.format(**values)

    def _select(self, ranked):
        return [
            {"id": row["id"], "score": row["score"]}
            for row in ranked if row["score"] >= self.config["min_score"]
        ][:self.config["top_k"]]

    def preview(self, answer, sources, candidates=None):
        if (not isinstance(answer, str) or not 1 <= len(answer) <= 2000
                or len(answer.splitlines()) != 1 or answer.splitlines()[0] != answer):
            raise ValueError("预览 answer 必须是1至2000字符的单行文本")
        if not isinstance(sources, list) or len(sources) > len(self.rules):
            raise ValueError("预览 sources 必须是有效规则编号列表")
        if any(not isinstance(source, str) or source not in self._rule_ids for source in sources):
            raise ValueError("预览 sources 包含无效规则编号")
        if len(set(sources)) != len(sources):
            raise ValueError("预览 sources 不能重复")
        if candidates is None:
            candidates = []
        if not isinstance(candidates, list) or len(candidates) > len(self.rules):
            raise ValueError("预览 candidates 必须是规则候选列表")
        previous_score = 1.0
        seen = set()
        for candidate in candidates:
            if not isinstance(candidate, dict) or set(candidate) != {"id", "score"}:
                raise ValueError("候选条目必须含 id 和 score")
            candidate_id, score = candidate["id"], candidate["score"]
            if not isinstance(candidate_id, str) or candidate_id not in self._rule_ids or candidate_id in seen:
                raise ValueError("候选编号必须有效且不能重复")
            if type(score) not in (int, float) or not -1 <= score <= previous_score:
                raise ValueError("候选分数须为[-1,1]内有限数值且按分数降序排列")
            seen.add(candidate_id)
            previous_score = score
        return {"answer": self._render(answer, sources), "sources": sources, "candidates": self._select(candidates)}

    def embed(self, texts):
        if not isinstance(texts, list) or not 1 <= len(texts) <= 4:
            raise ValueError("texts 必须是包含1至4个字符串的列表")
        texts = [validate_question(text) for text in texts]
        with self._lock:
            if not self._ready:
                raise ServiceNotReady("模型尚未就绪")
            vectors = _vectors(self._encoder, texts)
            self.query_encodes += len(texts)
            return {"dimension": DIMENSION, "vectors": vectors.tolist()}

    def _retrieve(self, question):
        query = _vectors(self._encoder, [question])[0]
        self.query_encodes += 1
        matrix = self._index
        if not self.config["reuse_index"]:
            matrix = _vectors(self._encoder, self._rule_texts)
            self.doc_encodes += len(self.rules)
        scores = np.clip(matrix @ query, -1, 1)
        return [{**self.rules[index], "score": float(scores[index])}
                for index in np.argsort(-scores, kind="stable")]

    def answer(self, question, borrow_branch=None, return_branch=None):
        question = validate_question(question)
        _validate_branch(borrow_branch, "borrow_branch")
        _validate_branch(return_branch, "return_branch")
        key = (question, borrow_branch, return_branch)
        with self._lock:
            if not self._ready or self._index is None:
                raise ServiceNotReady("模型或规则索引尚未就绪")
            if key in self._cache:
                self.cache_hits += 1
                self._cache.move_to_end(key)
                return copy.deepcopy(self._cache[key])
            ranked = self._retrieve(question)
            if self._policy is None:
                raise ServiceNotReady("归还咨询业务尚未实现")
            decision = self._policy(question, borrow_branch, return_branch, ranked)
            candidates = self._select(decision.get("ranked", ranked))
            sources = decision["sources"]
            if set(sources) - {row["id"] for row in candidates}:
                decision = {
                    "decision": "OUT_OF_SCOPE", "return_to": None, "missing_fields": [],
                    "sources": [], "content": "资料未收录该事项。",
                }
                candidates, sources = [], []
            result = {
                "decision": decision["decision"],
                "return_to": decision["return_to"],
                "missing_fields": decision["missing_fields"],
                "sources": sources,
                "answer": self._render(decision["content"], sources),
                "candidates": candidates,
            }
            capacity = self.config["cache_size"]
            if capacity:
                self._cache[key] = copy.deepcopy(result)
                if len(self._cache) > capacity:
                    self._cache.popitem(last=False)
            return result
