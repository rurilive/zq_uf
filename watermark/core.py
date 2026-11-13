"""按最大时间戳推进的水位。"""


class Window:
    def __init__(self, size=10):
        self.size = size
        self.watermark = None
        self.buffer = []
        self.closed = []

    def push(self, event):
        self.buffer.append(event)
        if self.watermark is None or event["ts"] > self.watermark:
            self.watermark = event["ts"]

    def advance(self, ts):
        if self.watermark is None or ts > self.watermark:
            self.watermark = ts
        keep = []
        for event in self.buffer:
            if event["ts"] <= self.watermark - self.size:
                self.closed.append(event)
            else:
                keep.append(event)
        self.buffer = keep
        return list(self.closed)
