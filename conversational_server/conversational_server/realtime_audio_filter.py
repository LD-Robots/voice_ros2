import time
from dataclasses import dataclass

import numpy as np


def rms_dbfs(pcm_i16: np.ndarray) -> float:
    if pcm_i16.size == 0:
        return -120.0
    xf = pcm_i16.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(xf * xf) + 1e-12))
    return 20.0 * np.log10(rms + 1e-12)


def highpass_filter(pcm_i16: np.ndarray, cutoff_hz: float, sample_rate: int) -> np.ndarray:
    if cutoff_hz <= 0 or pcm_i16.size == 0:
        return pcm_i16

    rc = 1.0 / (2.0 * np.pi * cutoff_hz)
    dt = 1.0 / float(sample_rate)
    alpha = rc / (rc + dt)

    xf = pcm_i16.astype(np.float32)
    y = np.zeros_like(xf)
    y_prev = 0.0
    x_prev = 0.0

    for idx, sample in enumerate(xf):
        y[idx] = alpha * (y_prev + sample - x_prev)
        y_prev = y[idx]
        x_prev = sample

    return np.clip(y, -32768, 32767).astype(np.int16)


def zero_crossing_rate(pcm_i16: np.ndarray) -> float:
    if pcm_i16.size < 2:
        return 0.0
    signs = np.sign(pcm_i16)
    crossings = np.sum(np.abs(np.diff(signs))) / 2.0
    return float(crossings / max(1, pcm_i16.size - 1))


@dataclass(slots=True)
class PlaybackInputFilterConfig:
    min_rms_dbfs: float = -24.0
    highpass_hz: float = 300.0
    zcr_min: float = 0.05
    zcr_max: float = 0.35
    leak_margin_db: float = 6.0
    leak_decay_ms: int = 1200
    hits_required: int = 4
    hold_ms: int = 240


class PlaybackInputFilter:
    def __init__(self, config: PlaybackInputFilterConfig | None = None):
        self.config = config or PlaybackInputFilterConfig()
        self.reset()

    def reset(self):
        self.leak_baseline_dbfs = None
        self.last_leak_update_ms = 0
        self.pending_hits = 0
        self.last_voice_ms = 0
        self.gate_open = False

    def should_forward(
        self,
        pcm_i16: np.ndarray,
        *,
        sample_rate: int,
        now_ms: int | None = None,
    ) -> bool:
        now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        self._maybe_decay_leak(now_ms)
        self._refresh_gate(now_ms)

        if pcm_i16.size == 0:
            return self.gate_open

        rms = rms_dbfs(pcm_i16)
        rms_threshold = self.config.min_rms_dbfs
        if self.leak_baseline_dbfs is not None:
            rms_threshold = max(rms_threshold, self.leak_baseline_dbfs + self.config.leak_margin_db)

        if rms < rms_threshold:
            self._register_non_voice(rms, now_ms)
            return self.gate_open

        pcm_filtered = highpass_filter(pcm_i16, self.config.highpass_hz, sample_rate)
        zcr = zero_crossing_rate(pcm_filtered)
        if not (self.config.zcr_min <= zcr <= self.config.zcr_max):
            self._register_non_voice(rms, now_ms)
            return self.gate_open

        self.last_voice_ms = now_ms
        if not self.gate_open:
            self.pending_hits += 1
            if self.pending_hits >= self.config.hits_required:
                self.gate_open = True
                self.pending_hits = 0

        return self.gate_open

    def _register_non_voice(self, rms_db: float, now_ms: int):
        self.pending_hits = 0
        self._update_leak_baseline(rms_db, now_ms)
        self._refresh_gate(now_ms)

    def _refresh_gate(self, now_ms: int):
        if self.gate_open and (now_ms - self.last_voice_ms) > self.config.hold_ms:
            self.gate_open = False

    def _maybe_decay_leak(self, now_ms: int):
        if self.leak_baseline_dbfs is None:
            return
        if (now_ms - self.last_leak_update_ms) > self.config.leak_decay_ms:
            self.leak_baseline_dbfs = None
            self.last_leak_update_ms = now_ms

    def _update_leak_baseline(self, rms_db: float, now_ms: int):
        if not np.isfinite(rms_db) or rms_db <= -90.0:
            return

        if self.leak_baseline_dbfs is None:
            self.leak_baseline_dbfs = rms_db
        else:
            capped_rms = min(
                rms_db,
                self.leak_baseline_dbfs + self.config.leak_margin_db * 2.0,
            )
            alpha = 0.12
            self.leak_baseline_dbfs = (
                (1.0 - alpha) * self.leak_baseline_dbfs + alpha * capped_rms
            )

        self.last_leak_update_ms = now_ms
