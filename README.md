# rateadapt

按响应自适应限速：`acquire(site)` 取预算，`feedback(site, status, rtt)` 回报结果。

- 调速口径：档位 0.25~8 QPS。连续 10 次成功上调一档（慢涨）；最近 10 条反馈中 429 占比 ≥30% 一次下两档、成功率 <50% 下一档（快降）；样本不足 5 条不调整。
- 冷却口径：每次调整后 30 秒内不再调整（`reason="cooldown"`）；到顶/到底报 `at_cap`/`at_floor`。
- 预算：限速与并发共用一个令牌桶，容量 = 组内速率之和 × 4 秒；`join_group` 让站点分组共享预算，突发被 `budget_exhausted` 拒绝。
- 记录：`records(site)` 带响应码和耗时；`replay(site)` 回放调整记录；`rollback(site)` 回滚上一档。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
