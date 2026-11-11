"""Streaming audio resampler for the live pipeline.

The resampler uses linear interpolation over a buffer of input samples.  Only
the Python standard library is used.

Timestamps
----------
``push(pcm, timestamp)`` takes an absolute timestamp in *microseconds* for the
first sample of ``pcm``.  The unit is independent of the input sample rate, so
a rate switch only changes ``in_rate``; callers keep supplying timestamps from
the same clock.  Internally timestamps are converted to seconds and every
timestamp is authoritative: gaps are filled, overlaps are trimmed, and the
read phase is rebuilt from the timestamp rather than from the sample count.

Rate switches
-------------
Assigning ``in_rate`` flags a pending switch.  On the next ``push`` the samples
still buffered at the old rate are linearly rescaled to the new rate and the
fractional read position is rescaled with them, so the old tail joins the new
stream instead of being dropped.

Clock drift
-----------
``pull`` contains a small servo.  It predicts the buffered water level after
the requested pull, compares it with ``target_level_ms`` and nudges the
effective input/output ratio by ``drift_step`` per pull (bounded by
``drift_limit``).  A stream whose clock is slightly fast or slow therefore
converges to the target latency instead of slowly filling or emptying the
buffer.
"""

import math


class Resampler:
    """Linear-interpolation streaming resampler.

    Parameters
    ----------
    in_rate, out_rate:
        Nominal input and output sample rates.  ``in_rate`` may be changed at
        run time (see :attr:`in_rate`).
    target_level_ms:
        Desired buffer water level after the stream is primed.
    drift_step:
        Relative ratio adjustment applied per ``pull`` while the buffer level
        is outside the deadband.  Set to ``0`` to disable drift compensation.
    drift_limit:
        Maximum relative distance of the effective ratio from the nominal
        ratio (``0.005`` means +/- 0.5%, i.e. +/- 5000 ppm).
    drift_deadband_ms:
        The level band around ``target_level_ms`` in which no adjustment is
        made.
    gap_threshold_ms:
        Timestamp deltas smaller than this are treated as continuous.  Larger
        forward deltas insert silence; larger backward deltas trim duplicate
        input.
    fade_ms:
        Length of the fade-out/fade-in applied around an inserted gap.
    reset_threshold_ms:
        A backward timestamp jump larger than this is treated as a stream
        reset and clears stale buffered samples.
    max_buffer_ms:
        Safety cap on the buffer.  Compensation normally keeps the level near
        the target; if the consumer stops pulling, the oldest samples are
        dropped once this cap is reached.
    """

    STATE_VERSION = 1

    def __init__(
        self,
        in_rate: int = 48000,
        out_rate: int = 48000,
        *,
        target_level_ms: float = 20.0,
        drift_step: float = 2e-5,
        drift_limit: float = 5e-3,
        drift_deadband_ms: float = 2.0,
        gap_threshold_ms: float = 0.05,
        fade_ms: float = 2.0,
        reset_threshold_ms: float = 200.0,
        max_buffer_ms: float = 500.0,
    ):
        if in_rate <= 0 or out_rate <= 0:
            raise ValueError("sample rate must be positive")
        for name, value in (
            ("target_level_ms", target_level_ms),
            ("drift_step", drift_step),
            ("drift_limit", drift_limit),
            ("drift_deadband_ms", drift_deadband_ms),
            ("gap_threshold_ms", gap_threshold_ms),
            ("fade_ms", fade_ms),
            ("reset_threshold_ms", reset_threshold_ms),
            ("max_buffer_ms", max_buffer_ms),
        ):
            if value < 0:
                raise ValueError("%s must be non-negative" % name)

        self.out_rate = out_rate
        self._in_rate = in_rate
        self.base_ratio = in_rate / out_rate
        self._ratio_trim = 0.0
        self.ratio = self.base_ratio

        self.buffer = []          # buffered input samples, in input samples
        self.position = 0.0       # fractional read position inside ``buffer``
        self.last_timestamp = None
        self._prev_end_time = None  # absolute end of the last chunk, seconds

        # Per-stream state.
        self._pending_rate_change = None
        self._pending_fade_in = 0
        self._last_sample = 0.0
        self._input_time_s = 0.0
        self._input_samples = 0
        self._output_samples = 0
        self._gap_samples = 0
        self._overrun_samples = 0

        # Configuration.
        self.target_level_ms = target_level_ms
        self.drift_step = drift_step
        self.drift_limit = drift_limit
        self.drift_deadband_ms = drift_deadband_ms
        self.gap_threshold_ms = gap_threshold_ms
        self.fade_ms = fade_ms
        self.reset_threshold_ms = reset_threshold_ms
        self.max_buffer_ms = max_buffer_ms

    # ------------------------------------------------------------------
    # Public rate handling
    # ------------------------------------------------------------------
    @property
    def in_rate(self) -> int:
        return self._in_rate

    @in_rate.setter
    def in_rate(self, value: int):
        if value <= 0:
            raise ValueError("sample rate must be positive")
        if value == self._in_rate:
            return
        old_rate = self._in_rate
        if self.buffer:
            # Remember the rate at which the buffered samples were captured.
            if self._pending_rate_change is None:
                self._pending_rate_change = old_rate
            elif value == self._pending_rate_change:
                # Switched back before any new data arrived.
                self._pending_rate_change = None
        else:
            self._pending_rate_change = None
        self._in_rate = value
        self.base_ratio = value / self.out_rate
        self._ratio_trim = 0.0
        self.ratio = self.base_ratio

    def set_input_rate(self, rate: int):
        """Convenience alias for ``resampler.in_rate = rate``."""
        self.in_rate = rate

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    @property
    def buffer_level_samples(self) -> int:
        return len(self.buffer)

    @property
    def buffer_level_ms(self) -> float:
        return len(self.buffer) / float(self._in_rate) * 1000.0

    @property
    def phase(self) -> float:
        """Fractional input-sample read position inside the buffer."""
        return self.position

    @property
    def effective_ratio(self) -> float:
        return self.ratio

    @property
    def input_samples(self) -> int:
        return self._input_samples

    @property
    def output_samples(self) -> int:
        return self._output_samples

    @property
    def gap_samples(self) -> int:
        return self._gap_samples

    @property
    def overrun_samples(self) -> int:
        return self._overrun_samples

    # ------------------------------------------------------------------
    # Streaming interface
    # ------------------------------------------------------------------
    def push(self, pcm, timestamp: int):
        """Push a chunk of input PCM starting at absolute ``timestamp`` (us)."""
        if timestamp is None:
            raise ValueError("timestamp is required")
        samples = [float(x) for x in pcm]

        if self._pending_rate_change is not None:
            self._rescale_buffer(self._pending_rate_change, self._in_rate)
            self._pending_rate_change = None

        stream_start = timestamp / 1_000_000.0
        original_end = stream_start + len(samples) / float(self._in_rate)

        if self._prev_end_time is not None:
            delta = stream_start - self._prev_end_time
            threshold = self.gap_threshold_ms / 1000.0

            if delta > threshold:
                # Packet loss: fill the missing time with faded silence.
                gap_n = int(round(delta * self._in_rate))
                if gap_n > 0:
                    self._append_gap(gap_n)
            elif delta < -threshold:
                overlap = -delta
                if overlap > self.reset_threshold_ms / 1000.0:
                    # A large backward jump is a new stream, not jitter.
                    self._reset_stream()
                else:
                    # New data starts in the past.  Drop the duplicate prefix
                    # so the already-played timeline stays monotonic.
                    skip_n = int(round(overlap * self._in_rate))
                    if skip_n >= len(samples):
                        self.last_timestamp = timestamp
                        return
                    samples = samples[skip_n:]

        self._append_chunk(samples)
        self._prev_end_time = original_end
        self.last_timestamp = timestamp
        self._input_time_s += len(samples) / float(self._in_rate)
        self._input_samples += len(samples)
        self._enforce_max_buffer()

    def pull(self, frames: int):
        """Pull up to ``frames`` output samples."""
        if frames < 0:
            raise ValueError("frames must be non-negative")
        if frames == 0:
            return []

        self._update_drift(frames)

        out = []
        ratio = self.ratio
        while len(out) < frames:
            index = int(self.position)
            frac = self.position - index
            # Floating-point ratio accumulation can leave a position a hair
            # below the next integer; snap it so chunk boundaries stay exact.
            if frac >= 1.0 - 1e-9:
                index += 1
                frac = 0.0
            if index + 1 >= len(self.buffer):
                break
            value = self.buffer[index] * (1.0 - frac) + self.buffer[index + 1] * frac
            out.append(int(value))
            self.position += ratio

        consumed = int(self.position)
        if consumed:
            self.buffer = self.buffer[consumed:]
            self.position -= consumed
            if not self.buffer:
                self.position = 0.0

        self._output_samples += len(out)
        return out

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _rescale_buffer(self, old_rate: int, new_rate: int):
        """Rescale buffered input samples from ``old_rate`` to ``new_rate``."""
        if not self.buffer or old_rate == new_rate:
            return
        if old_rate <= 0 or new_rate <= 0:
            raise ValueError("sample rate must be positive")

        old = list(self.buffer)
        factor = new_rate / float(old_rate)
        new_len = max(1, int(round(len(old) * factor)))
        new_buffer = []

        if len(old) == 1:
            new_buffer = [old[0]] * new_len
        else:
            for j in range(new_len):
                x = j / factor
                index = int(x)
                if index >= len(old) - 1:
                    new_buffer.append(old[-1])
                else:
                    frac = x - index
                    new_buffer.append(
                        old[index] * (1.0 - frac) + old[index + 1] * frac
                    )

        self.buffer = new_buffer
        self.position *= factor

    def _append_chunk(self, samples):
        if not samples:
            return
        if self._pending_fade_in:
            count = min(self._pending_fade_in, len(samples))
            for i in range(count):
                samples[i] *= i / float(count)
            self._pending_fade_in -= count
        self.buffer.extend(samples)
        self._last_sample = samples[-1]

    def _append_gap(self, gap_n: int):
        """Append faded silence for a timestamp gap of ``gap_n`` samples."""
        if gap_n <= 0:
            return
        fade_n = min(
            gap_n, int(round(self.fade_ms * self._in_rate / 1000.0))
        )
        tail = self.buffer[-1] if self.buffer else self._last_sample
        for i in range(fade_n):
            self.buffer.append(tail * (1.0 - (i + 1) / float(fade_n)))
        if gap_n > fade_n:
            self.buffer.extend([0.0] * (gap_n - fade_n))
        self._pending_fade_in = fade_n
        self._last_sample = 0.0
        self._gap_samples += gap_n

    def _reset_stream(self):
        """Discard stale samples after a large backward timestamp jump."""
        self.buffer = []
        self.position = 0.0
        self._ratio_trim = 0.0
        self.ratio = self.base_ratio
        self._pending_fade_in = 0
        self._last_sample = 0.0
        self._prev_end_time = None

    def _enforce_max_buffer(self):
        max_n = int(self.max_buffer_ms * self._in_rate / 1000.0)
        if max_n <= 0 or len(self.buffer) <= max_n:
            return
        drop = len(self.buffer) - max_n
        self.buffer = self.buffer[drop:]
        self.position = max(0.0, self.position - drop)
        self._overrun_samples += drop

    def _update_drift(self, frames: int):
        """Nudge the effective ratio toward the target buffer level.

        The comparison uses the level predicted after this pull rather than
        the instantaneous level, so bursty push/pull patterns do not bias the
        target by one chunk size.
        """
        if self._input_time_s < self.target_level_ms / 1000.0:
            # Not primed yet: leave the nominal ratio untouched so startup
            # output stays bit-exact for continuous streams.
            self._ratio_trim = 0.0
            self.ratio = self.base_ratio
            return

        if len(self.buffer) < 2:
            available = 0
        else:
            available = int(math.ceil(
                (len(self.buffer) - 1) / self.ratio
            ))
        if frames > available:
            # Underrun: the ratio cannot manufacture missing input, so leave
            # the servo alone until the stream has real samples again.
            return

        level_after = (
            len(self.buffer) - frames * self.ratio
        ) / float(self._in_rate) * 1000.0
        deadband = self.drift_deadband_ms
        if level_after > self.target_level_ms + deadband:
            self._ratio_trim = min(
                self.drift_limit, self._ratio_trim + self.drift_step
            )
        elif level_after < self.target_level_ms - deadband:
            self._ratio_trim = max(
                -self.drift_limit, self._ratio_trim - self.drift_step
            )
        # Inside the deadband keep the current trim (zero drift correction).
        self.ratio = self.base_ratio * (1.0 + self._ratio_trim)

    # ------------------------------------------------------------------
    # State serialization
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        """Return a JSON-serializable snapshot of all mutable state."""
        return {
            "version": self.STATE_VERSION,
            "in_rate": self._in_rate,
            "out_rate": self.out_rate,
            "base_ratio": self.base_ratio,
            "ratio": self.ratio,
            "ratio_trim": self._ratio_trim,
            "buffer": list(self.buffer),
            "position": self.position,
            "last_timestamp": self.last_timestamp,
            "prev_end_time": self._prev_end_time,
            "pending_rate_change": self._pending_rate_change,
            "pending_fade_in": self._pending_fade_in,
            "last_sample": self._last_sample,
            "input_time_s": self._input_time_s,
            "input_samples": self._input_samples,
            "output_samples": self._output_samples,
            "gap_samples": self._gap_samples,
            "overrun_samples": self._overrun_samples,
            "config": {
                "target_level_ms": self.target_level_ms,
                "drift_step": self.drift_step,
                "drift_limit": self.drift_limit,
                "drift_deadband_ms": self.drift_deadband_ms,
                "gap_threshold_ms": self.gap_threshold_ms,
                "fade_ms": self.fade_ms,
                "reset_threshold_ms": self.reset_threshold_ms,
                "max_buffer_ms": self.max_buffer_ms,
            },
        }

    def restore(self, state: dict):
        """Restore a snapshot produced by :meth:`snapshot`."""
        version = state.get("version", self.STATE_VERSION)
        if version != self.STATE_VERSION:
            raise ValueError("unsupported resampler state version: %r" % version)

        self._in_rate = int(state["in_rate"])
        self.out_rate = int(state["out_rate"])
        self.base_ratio = state.get(
            "base_ratio", self._in_rate / float(self.out_rate)
        )
        self._ratio_trim = state.get("ratio_trim", 0.0)
        self.ratio = state.get("ratio", self.base_ratio)
        self.buffer = [float(x) for x in state.get("buffer", [])]
        self.position = state.get("position", 0.0)
        self.last_timestamp = state.get("last_timestamp")
        self._prev_end_time = state.get("prev_end_time")
        self._pending_rate_change = state.get("pending_rate_change")
        self._pending_fade_in = state.get("pending_fade_in", 0)
        self._last_sample = state.get("last_sample", 0.0)
        self._input_time_s = state.get("input_time_s", 0.0)
        self._input_samples = state.get("input_samples", 0)
        self._output_samples = state.get("output_samples", 0)
        self._gap_samples = state.get("gap_samples", 0)
        self._overrun_samples = state.get("overrun_samples", 0)

        config = state.get("config", {})
        for name in (
            "target_level_ms",
            "drift_step",
            "drift_limit",
            "drift_deadband_ms",
            "gap_threshold_ms",
            "fade_ms",
            "reset_threshold_ms",
            "max_buffer_ms",
        ):
            if name in config:
                setattr(self, name, config[name])
