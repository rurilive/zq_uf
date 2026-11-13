# latejoin

按事件时间把事实表关联到多版本维表，纯 Python 标准库。

规则：
- 维表按 `effective_time`（生效时间）保留全部版本；事实按 `event_time`
  （发生时间）匹配生效时间不晚于它的最新版本，历史口径不被新版本改写。
- 维表按传入顺序到达；某版本生效时间早于同主键已到达的最大生效时间即
  判定为迟到，迟到版本回补受影响的事实，并在 `stats["backfill_batches"]`
  记录每个回补批次（维表行、影响条数、事实明细）。
- 关联键为空或无可匹配版本时保留事实行，标记 `matched=False`，不丢弃。
- `join(facts, dims)` 返回结果行列表（每行含 `dim`/`matched`/`backfilled`），
  `.stats` 给出匹配率、回补条数和未匹配样本（最多 5 条）。

运行：

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
