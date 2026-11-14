# crawlband

现状：全局固定速率，任务共用额度。

问题：大文件占满带宽，突发无上限，结束不回收。

目标：按任务限速，突发上限，借还和结束归还。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 速率口径

- `RATE`：全局总带宽，所有任务在用额度之和不得超过。
- `CONFIG["base_rate"]`：每任务基础速率，是任务自有额度，随取随有。
- `CONFIG["burst_limit"]`：每任务突发上限 = 自有在用 + 借入未还，超限拒绝。
- 任务当前速率 = `stats()[task]["rate"]` = 自有在用 + 借入未还。
- `take(task, amount)` 先用自有，再借同组；任一上限不满足抛 `RuntimeError` 并计入 `rejected`。

## 借还口径

- 同组任务（`CONFIG["groups"]`，缺省同组）的空闲自有额度可借出；未还借入上限 `CONFIG["borrow_limit"]`。
- 每笔借入带到期时间 `CONFIG["borrow_ttl"]` 秒，到期自动归还出借方。
- `give_back(task, amount)` 先还借入（先到期先还）再还自有；可重入，超还只清到 0。
- `finish(task)` 任务结束：归还全部借入与自有，并收回借出，可重复调用。
- `stats()` 输出各任务速率、借入 `borrowed_in`、借出 `lent_out`、拒绝数 `rejected`。
- 兼容：`band._used = 0` 等于 `reset()`，清空全部任务状态（配置保留）。
