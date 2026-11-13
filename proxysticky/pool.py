"""采集器的出口池，当前每次请求随机取一个代理。"""
import random

PROXIES = [
    "10.0.0.11:8080",
    "10.0.0.12:8080",
    "10.0.0.13:8080",
]


def acquire(site, session):
    """按站点和会话取一个出口，当前不看站点也不看会话。"""
    return random.choice(PROXIES)
