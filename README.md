# sitebudget2

跨进程采集预算：状态落盘 `state.json`，fcntl 文件锁原子扣减，接口仍是 `take(site, n)` / `give_back(site, n)`。

预算口径：`config.json` 的 `budgets` 配站点上限，未配置的站点不限额；`used` 为全部进程有效租约之和；超额时 `take` 不扣减，`wait` 返回可等待秒数（按租约到期顺序估算，`-1` 表示 n 超过上限、永远等不到）。

回收口径：每次 `take` 生成带 `lease_seconds` 到期时间的租约；进程崩溃未归还的额度，由任意进程在下一次操作或 `reclaim()` 时按到期时间惰性回收，恢复后额度能对上。所有写操作（take/give_back/reclaim/reload）追加到 `events.log` 并带时间戳，`stats()` 按 `used == take - give_back - reclaim` 跨进程对账。

预算热加载：改 `config.json` 即生效（按 mtime 探测）。状态目录用环境变量 `SITEBUDGET2_DIR` 指定，默认 `./.sitebudget2`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
