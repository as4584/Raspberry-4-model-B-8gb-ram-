"""ONNX intent model for the Brain: MiniLM-L6 sentence encoder (int8, ~23 MB, Apache-2.0)
+ a linear head trained on the lab's synthetic receptionist phrases. ~130 ms on a Pi 4.
Optional runtime (pip install .[brain]); load() raises if the bundle or runtime is missing."""
from __future__ import annotations

from pathlib import Path


class OnnxIntents:
    def __init__(self, bundle: str, threads: int = 2):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        b = Path(bundle)
        o = ort.SessionOptions(); o.intra_op_num_threads = threads; o.log_severity_level = 3
        self._np = np
        self._s = ort.InferenceSession(str(b / "encoder.int8.onnx"), o, providers=["CPUExecutionProvider"])
        self._tok = Tokenizer.from_file(str(b / "tokenizer" / "tokenizer.json")); self._tok.enable_truncation(64)
        head = np.load(b / "intent_head.npz")
        self._W, self._b, self._classes = head["W"], head["b"], [str(c) for c in head["classes"]]

    def classify(self, text: str) -> tuple[str, float]:
        np = self._np
        e = self._tok.encode(text)
        v = self._s.run(None, {"input_ids": np.array([e.ids], dtype=np.int64),
                               "attention_mask": np.array([e.attention_mask], dtype=np.int64)})[0][0]
        z = v @ self._W.T + self._b
        p = np.exp(z - z.max()); p /= p.sum()
        k = int(p.argmax())
        return self._classes[k], float(p[k])


def load(bundle: str) -> OnnxIntents:
    return OnnxIntents(bundle)
