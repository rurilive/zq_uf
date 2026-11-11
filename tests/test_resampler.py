import json
import math
import unittest

from audioresample import Resampler


class ResamplerTest(unittest.TestCase):
    def test_same_rate(self):
        resampler = Resampler(48000, 48000)
        resampler.push([0, 10, 20, 30, 40], 0)
        self.assertEqual(resampler.pull(3), [0, 10, 20])

    def test_upsample_count(self):
        resampler = Resampler(24000, 48000)
        resampler.push(list(range(100)), 0)
        self.assertGreater(len(resampler.pull(20)), 0)

    def test_downsample_count(self):
        resampler = Resampler(48000, 24000)
        resampler.push(list(range(100)), 0)
        self.assertGreater(len(resampler.pull(10)), 0)

    def test_snapshot_restore(self):
        resampler = Resampler(48000, 48000)
        resampler.push([1, 2, 3, 4], 0)
        state = resampler.snapshot()
        resampler.pull(1)
        resampler.restore(state)
        self.assertEqual(resampler.pull(1), [1])


class RateSwitchTest(unittest.TestCase):
    def test_switch_rescales_buffered_tail_and_keeps_phase(self):
        resampler = Resampler(48000, 48000, drift_step=0.0)
        first = [100] * 480  # 10 ms at 48 kHz
        resampler.push(first, 0)
        self.assertEqual(resampler.buffer_level_samples, 480)

        resampler.in_rate = 44100
        self.assertEqual(resampler.snapshot()["in_rate"], 44100)

        # Same absolute 10 ms boundary, expressed in the new rate's samples.
        second = [200] * 441
        resampler.push(second, 10000)

        # The 480 old samples were rescaled to 441 instead of being dropped.
        self.assertEqual(resampler.buffer_level_samples, 882)
        self.assertAlmostEqual(resampler.buffer_level_ms, 20.0, places=6)
        self.assertEqual(resampler.phase, 0.0)
        self.assertEqual(resampler.buffer[:441], [100.0] * 441)
        self.assertEqual(resampler.buffer[441:], [200.0] * 441)

        out = resampler.pull(520)
        self.assertEqual(len(out), 520)
        self.assertEqual(out[:479], [100] * 479)
        self.assertGreater(out[479], 100)
        self.assertLess(out[479], 200)
        self.assertEqual(out[480], 200)  # new-rate samples align at boundary

        # 520 output frames at 48 kHz consume 520 * 44100/48000 = 477.75
        # input samples; 477 are trimmed and the fractional phase remains.
        self.assertAlmostEqual(resampler.phase, 0.75, places=4)
        self.assertAlmostEqual(resampler.buffer_level_ms, 9.2, delta=0.5)
        self.assertEqual(resampler.input_samples, 480 + 441)
        self.assertEqual(resampler.output_samples, 520)

    def test_switch_back_before_new_data(self):
        resampler = Resampler(48000, 48000)
        resampler.push([5] * 480, 0)
        resampler.in_rate = 44100
        resampler.in_rate = 48000
        self.assertIsNone(resampler.snapshot()["pending_rate_change"])
        resampler.push([6] * 480, 10000)
        self.assertEqual(resampler.buffer_level_samples, 960)
        self.assertEqual(resampler.buffer[:480], [5.0] * 480)
        self.assertEqual(resampler.buffer[480:], [6.0] * 480)


class TimestampJumpTest(unittest.TestCase):
    def test_forward_gap_inserts_silence_and_realigns(self):
        resampler = Resampler(48000, 48000, fade_ms=0.0, drift_step=0.0)
        resampler.push([100] * 480, 0)
        # 10 ms of input is missing between chunks.
        resampler.push([200] * 480, 20000)

        self.assertEqual(resampler.gap_samples, 480)
        self.assertEqual(resampler.buffer_level_samples, 1440)
        self.assertAlmostEqual(resampler.buffer_level_ms, 30.0, places=6)

        out = resampler.pull(1200)
        self.assertEqual(len(out), 1200)
        self.assertEqual(out[:480], [100] * 480)
        self.assertEqual(out[480:960], [0] * 480)
        self.assertEqual(out[960], 200)
        self.assertEqual(out[-1], 200)
        self.assertEqual(resampler.buffer_level_samples, 240)
        self.assertEqual(resampler.phase, 0.0)
        self.assertEqual(resampler.input_samples, 960)
        self.assertEqual(resampler.output_samples, 1200)

    def test_gap_uses_fade_out_and_fade_in(self):
        resampler = Resampler(
            48000, 48000, fade_ms=2.0, drift_step=0.0
        )
        resampler.push([100] * 480, 0)
        resampler.push([200] * 480, 20000)

        out = resampler.pull(1100)
        fade_n = 96  # 2 ms at 48 kHz
        # Fade-out ramps from the old level to silence.
        self.assertLess(out[480], 100)
        self.assertEqual(out[480 + fade_n], 0)
        self.assertEqual(out[480:480 + fade_n + 1],
                         sorted(out[480:480 + fade_n + 1], reverse=True))
        # Fade-in starts from silence before the new chunk.
        self.assertEqual(out[960], 0)
        self.assertGreater(out[960 + fade_n], 190)
        self.assertEqual(out[1056], 200)

    def test_small_backward_jump_trims_duplicate_prefix(self):
        resampler = Resampler(48000, 48000, fade_ms=0.0, drift_step=0.0)
        resampler.push([100] * 480, 0)
        # This chunk really starts 4 ms before the previous chunk ended;
        # its duplicate prefix must be discarded.
        resampler.push([200] * 480, 6000)

        self.assertEqual(resampler.gap_samples, 0)
        self.assertEqual(resampler.buffer_level_samples, 768)
        self.assertAlmostEqual(resampler.snapshot()["prev_end_time"],
                               0.016, places=9)

        out = resampler.pull(767)
        self.assertEqual(len(out), 767)
        self.assertEqual(out[:480], [100] * 480)
        self.assertEqual(out[480:], [200] * 287)
        self.assertEqual(resampler.buffer_level_samples, 1)

    def test_large_backward_jump_resets_stream(self):
        resampler = Resampler(48000, 48000, drift_step=0.0)
        resampler.push([100] * 480, 1_000_000)
        resampler.push([200] * 480, 0)

        self.assertEqual(resampler.buffer_level_samples, 480)
        self.assertEqual(resampler.buffer, [200.0] * 480)
        self.assertAlmostEqual(resampler.snapshot()["prev_end_time"],
                               0.01, places=9)
        out = resampler.pull(479)
        self.assertEqual(out, [200] * 479)


class DriftTest(unittest.TestCase):
    def _run(self, drift_step):
        resampler = Resampler(
            44100,
            48000,
            target_level_ms=20.0,
            drift_step=drift_step,
            drift_limit=5e-3,
            drift_deadband_ms=1.0,
        )
        timestamp = 0
        for _ in range(3000):
            resampler.push([0.0] * 441, timestamp)
            timestamp += 10000
            resampler.pull(479)
        return resampler

    def test_compensation_keeps_water_level_on_long_run(self):
        controlled = self._run(1e-4)
        uncontrolled = self._run(0.0)

        # Without compensation a 479-frame pull consumes only ~440.08 input
        # samples, so ~0.92 samples accumulate each 10 ms tick.
        self.assertGreater(uncontrolled.buffer_level_ms, 50.0)
        self.assertLess(controlled.buffer_level_ms, 35.0)
        self.assertGreater(controlled.buffer_level_ms, 5.0)
        self.assertLess(controlled.buffer_level_ms,
                        uncontrolled.buffer_level_ms)

        self.assertEqual(controlled.input_samples, 3000 * 441)
        self.assertEqual(controlled.output_samples, 3000 * 479)
        self.assertLessEqual(
            abs(controlled.effective_ratio / controlled.base_ratio - 1.0),
            controlled.drift_limit,
        )


class RestoreTest(unittest.TestCase):
    def test_json_round_trip_and_continuation_match(self):
        first = Resampler(
            44100,
            48000,
            target_level_ms=20.0,
            drift_step=1e-4,
            drift_deadband_ms=1.0,
        )
        timestamp = 0
        chunk = [int(500 * math.sin(i / 20.0)) for i in range(441)]
        for _ in range(100):
            first.push(chunk, timestamp)
            timestamp += 10000
            first.pull(479)

        state = json.loads(json.dumps(first.snapshot()))
        second = Resampler(44100, 48000)
        second.restore(state)

        self.assertEqual(second.in_rate, first.in_rate)
        self.assertEqual(second.buffer_level_samples, first.buffer_level_samples)
        self.assertAlmostEqual(second.phase, first.phase, places=12)
        self.assertAlmostEqual(second.effective_ratio,
                               first.effective_ratio, places=12)

        follow = [int(800 * math.cos(i / 15.0)) for i in range(441)]
        for _ in range(50):
            first.push(follow, timestamp)
            second.push(follow, timestamp)
            self.assertEqual(first.pull(479), second.pull(479))
            self.assertAlmostEqual(first.phase, second.phase, places=12)
            self.assertAlmostEqual(first.buffer_level_ms,
                                   second.buffer_level_ms, places=12)
            timestamp += 10000

        self.assertEqual(first.snapshot(), second.snapshot())

    def test_restore_keeps_pending_rate_change(self):
        resampler = Resampler(48000, 48000)
        resampler.push([1] * 480, 0)
        resampler.in_rate = 44100
        state = json.loads(json.dumps(resampler.snapshot()))

        restored = Resampler(48000, 48000)
        restored.restore(state)
        restored.push([2] * 441, 10000)

        self.assertEqual(restored.in_rate, 44100)
        self.assertEqual(restored.buffer_level_samples, 882)
        self.assertEqual(restored.buffer[:441], [1.0] * 441)
        self.assertEqual(restored.buffer[441:], [2.0] * 441)


if __name__ == "__main__":
    unittest.main()
