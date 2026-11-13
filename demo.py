from watermark import Window

window = Window(size=5, quantile=0.8,
                allowed_lateness={"sensor": 2, "default": 0},
                idle_timeout=10)

# 重放一段乱序流：("push", 事件) 或 ("advance", 处理时间)
stream = [
    ("push", {"ts": 1, "source": "sensor"}),
    ("push", {"ts": 3, "source": "sensor"}),
    ("push", {"ts": 2, "source": "sensor"}),  # 乱序到达
    ("push", {"ts": 8, "source": "other"}),
    ("advance", 6),                           # 水位=8，关闭 [0,5)
    ("push", {"ts": 4, "source": "sensor"}),  # 窗口已关 -> 侧输出
    ("advance", 10),                          # 有事件流动，水位不跳
    ("advance", 20),                          # 空闲超时，按处理时间推进
]

for action, payload in stream:
    if action == "push":
        window.push(payload)
        print(f"push    {payload}  watermark={window.watermark}")
    else:
        closed = window.advance(payload)
        print(f"advance {payload:>3}  closed={closed}  watermark={window.watermark}")

print("side_output:", window.side_output)
