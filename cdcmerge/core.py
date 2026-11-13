"""按到达顺序覆盖的变更合并。"""


class Merger:
    def __init__(self):
        self.rows = {}

    def apply(self, events):
        for event in events:
            key = event.get("key")
            if event.get("op") == "delete":
                self.rows.pop(key, None)
            else:
                self.rows[key] = event.get("value")
        return self.snapshot()

    def snapshot(self):
        return dict(self.rows)
