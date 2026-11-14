# filequeue

纯 Python 标准库实现的持久化文件队列，接口保持 `put(msg)`、`get(consumer)`、`ack(msg_id)`，
支持多消费者互斥、可见性超时重投、死信与幂等确认。

## 用法

```python
from filequeue import configure, put, get, ack, stats

# 数据目录即队列本身，目录存在则恢复历史消息（重启不丢状态）
configure("./queue_data", visibility_timeout=30.0, max_deliveries=5)

msg_id = put({"id": 1, "body": "订单支付"})  # 落盘，返回递增序号
msg = get("consumer-a")                       # 返回带 msg_id/deliveries 的消息副本
ack(msg["msg_id"])                            # 处理成功后确认并删除
print(stats())  # {'visible': 0, 'invisible': 0, 'dead': 0, 'redeliveries': 0}
```

## 可见性口径

- `put` 的消息带序号与 SHA-256 校验和，原子落盘到队列目录；默认超时 0 仅为兼容旧行为，生产请显式配置正数。
- `get` 取到的消息进入不可见状态并记录 `visible_at`，同一条消息同一时刻只被一个消费者持有（flock + 进程内锁）。
- 超时未 `ack` 视为消费者崩溃，消息自动恢复可见并可被任意消费者重投，`deliveries` 与 `stats()['redeliveries']` 递增。
- `ack` 只删除当前仍在租约中的消息；对已确认、已重投到期、不存在的 msg_id 重复确认返回 `False` 且不报错（幂等）。
- 校验和损坏的消息不会被投递，直接标记死信。

## 死信口径

- 投递次数达到 `max_deliveries`（默认 5）后租约再到期，消息不再重投，状态置为 `dead` 并保留在原文件中。
- 死信消息带 `dead_reason`：`max_deliveries_exceeded` 或 `checksum_mismatch`，需人工介入排查后再删除。
- `stats()` 输出四类计数：`visible`（可投递）、`invisible`（租约中）、`dead`（死信）、`redeliveries`（全队列累计重投次数）。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

`demo.py` 用子进程 `os._exit` 模拟消费者崩溃，依次演示不可见互斥、超时重投、幂等确认与死信。
