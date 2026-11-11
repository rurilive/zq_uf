"""Simulate a live stream that switches sample rate and loses packets."""

import math

from audioresample import Resampler

OUT_RATE = 48000
FRAME_RATE_HZ = 440.0
TICK_S = 0.01                      # 10 ms processing tick
TARGET_FRAMES = int(OUT_RATE * TICK_S)


def chunk_for(rate, start_s):
    """Continuous 440 Hz tone sampled at ``rate`` for one tick."""
    n = int(round(rate * TICK_S))
    return [
        int(12000 * math.sin(2 * math.pi * FRAME_RATE_HZ * (start_s + i / rate)))
        for i in range(n)
    ]


def main():
    resampler = Resampler(48000, OUT_RATE, target_level_ms=20.0)
    input_rate = 48000
    now_s = 0.0
    deficit = 0          # output frames that could not be delivered on time
    total_pushed = 0
    total_pulled = 0

    def run(ticks, drops=(), label=""):
        nonlocal now_s, deficit, total_pushed, total_pulled
        underruns = 0
        for tick in range(ticks):
            timestamp_us = int(round(now_s * 1_000_000))
            if tick not in drops:
                pcm = chunk_for(input_rate, now_s)
                resampler.push(pcm, timestamp_us)
                total_pushed += len(pcm)
            want = TARGET_FRAMES + deficit
            got = resampler.pull(want)
            deficit = want - len(got)
            if len(got) < TARGET_FRAMES:
                underruns += 1
            total_pulled += len(got)
            now_s += TICK_S
        print(
            "%-24s in=%5d  gaps=%5d  level=%6.2f ms  ratio=%.6f  underruns=%d"
            % (label, input_rate, resampler.gap_samples,
               resampler.buffer_level_ms, resampler.effective_ratio,
               underruns)
        )

    print("timestamp unit: microseconds (rate independent)\n")

    # Pre-roll two ticks so the stream starts at the target latency.
    for _ in range(2):
        pcm = chunk_for(input_rate, now_s)
        resampler.push(pcm, int(round(now_s * 1_000_000)))
        total_pushed += len(pcm)
        now_s += TICK_S
    print("pre-roll: %.2f ms buffered\n" % resampler.buffer_level_ms)

    run(50, label="48 kHz steady")
    input_rate = 44100
    resampler.in_rate = input_rate
    run(40, label="switch -> 44.1 kHz")
    run(40, drops=(10, 11), label="44.1 kHz + packet loss")
    input_rate = 48000
    resampler.in_rate = input_rate
    run(50, label="switch -> 48 kHz")

    print()
    print("input samples pushed :", total_pushed,
          "(resampler accepted %d)" % resampler.input_samples)
    print("output samples pulled:", total_pulled,
          "(resampler produced %d)" % resampler.output_samples)
    print("gap silence inserted :", resampler.gap_samples)
    print("final buffer level   : %.2f ms" % resampler.buffer_level_ms)
    print("final phase          : %.4f input samples" % resampler.phase)
    print("snapshot keys        :", ", ".join(sorted(resampler.snapshot())))


if __name__ == "__main__":
    main()
