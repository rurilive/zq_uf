# sitequarantine

现状：问题站点只记原因，探测一律返回可用。

问题：样本不保留，恢复靠人工，隔离状态不共享。

目标：隔离带原因、样本和到期，低频探测后连续成功才解除。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

口径：
- `quarantine(host, reason)`：隔离并记录原因（自动分类，`stats()` 统计）、样本和到期时间，`configure(ttl=...)` 配置到期。
- `probe(host)`：未到期或退避中不探测；探测间隔按隔离次数指数退避（`probe_interval * backoff_factor^(次数-1)`）。
- 连续成功 `release_after` 次才解除，进观察期降权放行（`observation_window`/`observation_weight`），期满无故障才完全放量。
- 任何探测失败立即重新隔离并延长到期（`extend_factor` 指数延长）；样本只保留最近 `sample_limit` 条。
- 状态落盘 `state_path`（JSON，默认 `$SITEQUARANTINE_STATE` 或临时目录），多进程共享，后写覆盖。
- `override(host, "release"|"isolate", operator=...)` 手工覆盖，全部写审计，`audit()` 可查。
