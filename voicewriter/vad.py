"""Streaming Silero voice activity detection, using the model bundled with faster-whisper."""

import os

import faster_whisper
import numpy as np
import onnxruntime

FRAME = 512  # 32 ms at 16 kHz
_CONTEXT = 64


class StreamingVad:
    def __init__(self):
        path = os.path.join(os.path.dirname(faster_whisper.__file__), "assets", "silero_vad_v6.onnx")
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self._session = onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"], sess_options=opts)
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(_CONTEXT, dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        """Speech probability for one 512-sample frame."""
        x = np.concatenate([self._context, frame]).reshape(1, -1).astype(np.float32)
        out, self._h, self._c = self._session.run(None, {"input": x, "h": self._h, "c": self._c})
        self._context = frame[-_CONTEXT:]
        return float(out.reshape(-1)[0])
