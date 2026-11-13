"""模拟下游中断：批次 2 推到一半故障，用断点 token 续推，确认后推进水位。"""
from pushdown import PushError, push, reset, resume, set_transport

reset()
inbox = []

first = push([{"id": 1}, {"id": 2}])
print(first)                     # 批次 1 全量推送
print(resume(first["token"]))    # 下游确认批次 1，水位推进到 1

calls = {"n": 0}


def flaky(row):
    calls["n"] += 1
    if calls["n"] == 2:
        raise ConnectionError("downstream unavailable")
    inbox.append(row)


set_transport(flaky)

checkpoint = None
try:
    push([{"id": 3}, {"id": 4}, {"id": 5}])
except PushError as exc:
    checkpoint = exc.token
    print("interrupted:", exc)

continued = resume(checkpoint)       # 从断点续推剩余 2 条，不重复推
print(continued)
print(resume(continued["token"]))    # 下游确认批次 2，水位推进到 2
print("inbox:", [row["id"] for row in inbox])
