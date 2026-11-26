"""Real CPU Paddle encoder. No cache, fake model, download, or fallback."""
from pathlib import Path
import os
import threading

import numpy as np

DIMENSION = 312


class PaddleEncoder:
    def __init__(self, model_path: Path, max_length: int = 128, device: str = "cpu"):
        self.model_path = Path(model_path).resolve()
        self.max_length = max_length
        self.device = device
        self._lock = threading.RLock()
        self._model = None

    def load(self):
        if self._model is not None:
            return
        if self.device != "cpu":
            raise ValueError("本题只使用 cpu")
        for filename in ("model_state.pdparams", "config.json", "vocab.txt"):
            if not (self.model_path / filename).is_file():
                raise FileNotFoundError("本地模型文件缺失：" + filename)
        os.environ["OMP_NUM_THREADS"] = "1"
        os.environ["CPU_NUM"] = "1"
        os.environ.setdefault("GLOG_minloglevel", "2")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        try:
            import paddle
            from paddlenlp.transformers import ErnieModel, ErnieTokenizer
            paddle.set_device("cpu")
            tokenizer = ErnieTokenizer.from_pretrained(str(self.model_path))
            model = ErnieModel.from_pretrained(str(self.model_path))
            model.eval()
            if int(model.config["hidden_size"]) != DIMENSION:
                raise ValueError("模型输出维度必须为312")
            self._paddle, self._tokenizer, self._model = paddle, tokenizer, model
        except Exception as exc:
            raise RuntimeError("真实本地 Paddle 模型加载失败：" + str(exc)) from exc

    def encode(self, texts: list[str]) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("模型尚未加载")
        if not texts:
            return np.empty((0, DIMENSION), dtype=np.float32)
        batches = []
        with self._lock:
            for start in range(0, len(texts), 4):
                batch = texts[start:start + 4]
                inputs = self._tokenizer(text=[""] * len(batch), text_pair=batch,
                    max_length=self.max_length, truncation=True, padding="max_length", return_tensors="pd")
                with self._paddle.no_grad():
                    output = self._model(**inputs)
                    sequence = output.last_hidden_state if hasattr(output, "last_hidden_state") else output[0]
                    vectors = sequence[:, 0].numpy().astype(np.float32)
                norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                if not np.isfinite(vectors).all() or (norms < 1e-12).any():
                    raise RuntimeError("模型输出无效向量")
                batches.append(vectors / norms)
        return np.concatenate(batches).astype(np.float32, copy=False)
