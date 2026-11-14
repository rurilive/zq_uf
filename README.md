# filequeue

带可见性超时的持久化文件队列，纯 Python 标准库。接口：`put(msg)`、`get(consumer)`、`ack(msg_id)`，另有 `stats()` 和 `configure()`。

## 可见性与死信口径

- 每条消息落盘为 `.filequeue/NNNNNN.json`（可用 `configure(path=...)` 或环境变量 `FILEQUEUE_DIR` 改目录），文件含序号、状态、重投次数和 payload 的 SHA-256 校验和，重启后自动恢复；校验和不符的消息直接隔离为死信。
- 可见（visible）：在队列中等待被取，按序号先进先出。
- 不可见（invisible）：被某个消费者取走并持有，同一时刻只有一个持有者；`visibility_timeout` 秒内未确认则自动回到可见并重投，重投次数加一。
- 死信（dead）：重投次数达到 `max_deliveries` 上限后进入死信，不再投递。
- 确认幂等：`ack` 只确认不可见消息，重复确认或未知 id 静默返回 True，不报错。

`get` 返回的 dict 附带 `_msg_id`，用它调用 `ack`；无可见消息时 `get` 阻塞等待（可传 `timeout` 秒，超时返回 None）。`stats()` 输出可见、不可见、死信数量和累计重投次数。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
