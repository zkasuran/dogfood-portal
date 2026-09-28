"""Narration voice for the demo video: Kokoro-82M (Apache-2.0), run locally via
kokoro-onnx. Clips are cached on disk by a hash of (voice, speed, text) so
re-recording does not re-synthesize unchanged lines.

Model files (download once, about 350 MB):
    https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
    https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
"""
import hashlib
import os

import numpy as np
import soundfile as sf

VOICE = "af_heart"
SPEED = 1.0
SR = 24000


class Voice:
    def __init__(self, model_dir, cache_dir, voice=VOICE, speed=SPEED):
        self.model_dir = model_dir
        self.cache_dir = cache_dir
        self.voice = voice
        self.speed = speed
        self._kokoro = None
        os.makedirs(cache_dir, exist_ok=True)

    def _engine(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro

            self._kokoro = Kokoro(
                os.path.join(self.model_dir, "kokoro-v1.0.onnx"),
                os.path.join(self.model_dir, "voices-v1.0.bin"),
            )
        return self._kokoro

    def clip(self, spoken):
        """Return (wav_path, seconds) for the spoken text, synthesizing if needed."""
        key = hashlib.sha1(f"{self.voice}|{self.speed}|{spoken}".encode()).hexdigest()[:16]
        path = os.path.join(self.cache_dir, f"{key}.wav")
        if not os.path.exists(path):
            samples, sr = self._engine().create(
                spoken, voice=self.voice, speed=self.speed, lang="en-us"
            )
            samples = np.asarray(samples, dtype=np.float32)
            # Trim synthesis silence at both ends so timing is driven by speech.
            loud = np.flatnonzero(np.abs(samples) > 0.01)
            if loud.size:
                a = max(0, loud[0] - int(0.03 * sr))
                b = min(samples.size, loud[-1] + int(0.08 * sr))
                samples = samples[a:b]
            sf.write(path, samples, sr)
        info = sf.info(path)
        return path, info.frames / info.samplerate
