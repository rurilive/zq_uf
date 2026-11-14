"""出口轮换，当前每次随机。"""
import random

PROXIES = ["10.0.0.1:80", "10.0.0.2:80", "10.0.0.3:80"]


def acquire(session):
    """不看会话，每次随机。"""
    return random.choice(PROXIES)


def release(session, proxy, ok):
    return {"session": session, "ok": bool(ok)}
