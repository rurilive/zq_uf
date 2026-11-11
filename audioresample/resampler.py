"""A small streaming audio resampler used by the live pipeline baseline."""


class Resampler:
    def __init__(self, in_rate: int = 48000, out_rate: int = 48000):
        if in_rate <= 0 or out_rate <= 0:
            raise ValueError("sample rate must be positive")
        self.in_rate = in_rate
        self.out_rate = out_rate
        self.ratio = in_rate / out_rate
        self.buffer = []
        self.position = 0.0
        self.last_timestamp = None

    def push(self, pcm, timestamp: int):
        self.buffer.extend(int(x) for x in pcm)
        self.last_timestamp = timestamp

    def pull(self, frames: int):
        if frames < 0:
            raise ValueError("frames must be non-negative")
        out = []
        while len(out) < frames:
            index = int(self.position)
            if index + 1 >= len(self.buffer):
                break
            frac = self.position - index
            value = self.buffer[index] * (1.0 - frac) + self.buffer[index + 1] * frac
            out.append(int(value))
            self.position += self.ratio
        consumed = int(self.position)
        if consumed:
            self.buffer = self.buffer[consumed:]
            self.position -= consumed
        return out

    def snapshot(self) -> dict:
        return {
            "in_rate": self.in_rate,
            "out_rate": self.out_rate,
            "position": self.position,
            "buffer": list(self.buffer),
            "last_timestamp": self.last_timestamp,
        }

    def restore(self, state: dict):
        self.in_rate = state["in_rate"]
        self.out_rate = state["out_rate"]
        self.ratio = self.in_rate / self.out_rate
        self.position = state["position"]
        self.buffer = list(state["buffer"])
        self.last_timestamp = state.get("last_timestamp")
