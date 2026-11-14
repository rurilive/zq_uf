# urlqueue

采集链接优先级队列：老化提权 + 保底窗口 + 落盘恢复，纯标准库。
接口：`push(url, priority=5)` / `pop()`，另附 `stats()` 与 `configure()`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 分数口径

score = 基础优先级 + 等待秒数 / 60，即每等待 60 秒 +1 分，分高先出。
同分按入队顺序（先入先出）。老化加分 ≥1 分的条目计为提权，
见 `stats()["promoted"]`；当前分数见 `stats()["scores"]`。

## 保底与持久化口径

保底：每 5 次出队至少 1 次给等待最久的条目，即高优连续插队上限 4 次，
保底次数见 `stats()["guaranteed"]`。每次 push/pop 原子落盘
`urlqueue_state.json`（可用 `URLQUEUE_STATE` 或 `configure(state_path=)` 改），
重启自动恢复并按分数重排，恢复条数见 `stats()["restored"]`。
