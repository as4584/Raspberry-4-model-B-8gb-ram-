"""Silero voice-activity detection (2 MB ONNX, ~1.3 ms per 32 ms window on the Pi 4).
Tells a human voice from noise; the call engine combines it with its loudness rule."""
from __future__ import annotations



from receptionist.audio import pcm16_from_mulaw

WINDOW = 256          # samples per Silero step at 8 kHz (32 ms)
CONTEXT = 32          # samples of the previous window Silero v5 expects in front


class SileroVAD:
    def __init__(self, model_path: str):
        import onnxruntime as ort
        opts = ort.SessionOptions(); opts.intra_op_num_threads = 1; opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])

    def stream(self) -> "_Stream":
        return _Stream(self.session)


class _Stream:
    """One per call (Silero keeps recurrent state). feed() takes a 20 ms u-law frame and returns
    the latest speech probability."""
    def __init__(self, session):
        import numpy as np
        self.session = session
        self.state = np.zeros((2, 1, 128), np.float32)
        self.context = np.zeros(CONTEXT, np.float32)
        self.pending = np.zeros(0, np.float32)
        self.sr = np.array(8000, np.int64)
        self.p = 0.0

    def feed(self, frame: bytes) -> float:
        import numpy as np
        x = np.frombuffer(pcm16_from_mulaw(frame), "<i2").astype(np.float32) / 32768
        self.pending = np.concatenate([self.pending, x])
        while len(self.pending) >= WINDOW:
            w, self.pending = self.pending[:WINDOW], self.pending[WINDOW:]
            out, self.state = self.session.run(None, {"input": np.concatenate([self.context, w])[None],
                                                      "state": self.state, "sr": self.sr})
            self.context = w[-CONTEXT:]
            self.p = float(out[0][0])
        return self.p
