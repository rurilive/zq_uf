"""模拟消费者崩溃：可见性超时重投，超限进死信。"""

from filequeue import ack, configure, get, put, reset, stats

configure(visibility_timeout=0.5, max_deliveries=2)
reset()

put({"id": 1, "task": "send-email"})

# 消费者 c1 取到消息后“崩溃”，永远不做 ack
crashed = get("c1")
print("c1 取到后崩溃:", crashed)
print("崩溃瞬间:", stats())

# 可见性超时后，同一条消息自动重投给 c2
msg = get("c2", timeout=5)
print("c2 收到重投:", msg)
ack(msg["_msg_id"])
print("ack 后:", stats())

# 坏消息每次被取走消费者都崩溃，超过重投上限进入死信
put({"id": 2, "task": "poison"})
while True:
    msg = get("c-crash", timeout=2)
    if msg is None:
        break
    print("c-crash 取到又崩溃:", msg["_msg_id"], stats())
print("最终:", stats())
