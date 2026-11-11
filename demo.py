import math

from audioresample import Resampler

resampler = Resampler(44100, 48000)
pcm = [int(10000 * math.sin(i / 20.0)) for i in range(200)]
resampler.push(pcm, 0)
print(len(resampler.pull(100)), resampler.snapshot()["last_timestamp"])
