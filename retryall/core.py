"""固定三次重试。"""
import time


def call(fn, policy=None):
    policy = policy or {}
    attempts = policy.get("attempts", 3)
    last = None
    for _ in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last = exc
            time.sleep(0.01)
    raise last
