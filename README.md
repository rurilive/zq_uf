# crawlbudget

采集任务配额：按站点分配预算，纯标准库，多进程共享计数、原子扣减。

## 预算字段

- `LIMIT = 1000`：全局日安全上限，所有站点合计每天最多消耗的请求数，耗尽抛 `BudgetError`。
- `configure(site, daily=None, burst=None, window=None)`：按站点配置，未配置字段用默认值。
- `daily`：站点日额度，正整数，默认 `LIMIT`。
- `burst`：突发窗口内允许的请求数（令牌桶容量），默认 `None` 不限。
- `window`：突发窗口秒数，默认 `1.0`；令牌以 `burst/window` 个每秒匀速回填。

## 重置口径

- 日额度（站点与全局）按自然日（本地时区）重置，跨天时下次访问自动清零。
- 突发额度按令牌桶实时回填，无固定重置点。
- `reset()` 手动清空共享计数并恢复默认配置，仅供测试使用。

## 接口约定

- `consume(site, n=1)`：成功返回该站点当日已消耗量（int）；站点日额度或突发
  额度不足时不扣减，返回需要等待的秒数（float，日额度为距次日零点的秒数）；
  全局上限耗尽、`n` 超过站点容量（永远不可能满足）或状态文件损坏时抛
  `BudgetError`；参数非法抛 `ValueError`。
- `remaining(site)`：站点日剩余与全局剩余的较小值。
- `stats(site=None)`：输出消耗（spent）、剩余（remaining）、拒绝次数（rejected）。

## 多进程

计数存储在状态文件（默认 `$TMPDIR/crawlbudget_state.json`，可用环境变量
`CRAWLBUDGET_STATE` 覆盖），通过 `fcntl.flock`（Windows 用 `msvcrt.locking`）
独占锁完成读-改-写，保证跨进程原子扣减。配置在进程内存中，每个进程启动后
都需调用 `configure()`。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
