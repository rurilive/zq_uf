"""按 URL 字符串去重的最小实现。"""


def digest(record):
    return item_url(record)


def item_url(record):
    return record.get("url", "")


class Fingerprint:
    def __init__(self):
        self.seen_keys = set()

    def seen(self, key):
        if key in self.seen_keys:
            return True
        self.seen_keys.add(key)
        return False
