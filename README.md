# siteallow

面向采集任务的共享站点许可名单，纯 Python 标准库。

## 接口

- `block(host, reason, ttl, task=None)`：拉黑，`ttl` 为有效期秒数（必须为正），`task` 缺省为 `*`（所有任务）。
- `allowed(host, task=None)`：当前是否允许抓取。
- `unblock(host, task=None, reason="manual")`：人工解除；无生效记录抛 `StoreError`。
- `current(task=None)`：当前生效的名单（含原因、任务范围、到期时间）。
- `audit(host=None, task=None)`：审计记录，可按站点和任务过滤。

## 存储与一致性

- 名单落盘为 JSON，默认 `~/.siteallow/allowlist.json`，可用环境变量 `SITEALLOW_STORE` 覆盖。
- 写操作：进程内互斥锁 + fcntl 排他文件锁，先写临时文件再 `os.replace` 原子替换；读操作用共享锁。多进程读到一致结果。
- 审计为 JSONL 追加日志（`<store>.audit.jsonl`），拉黑 / 解除 / 到期各一条，含时间、原因、任务、pid。

## 到期口径

- `expires_at = 拉黑时刻 + ttl`（`time.time()` 墙钟秒）；`now >= expires_at` 即视为解除。
- 到期惰性生效：读取时即放行，记录在下一次写操作时被清除并补写 `expire` 审计。
- 任务范围：记录为 `*` 或与调用方 `task` 相同才生效；`allowed(host)` 不传任务时只认 `*` 记录。

## 错误

- 参数非法（空 host/reason、非正 ttl 等）抛 `ValueError`。
- 名单/审计文件损坏或读写失败、解除不存在的记录，抛 `siteallow.StoreError`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
