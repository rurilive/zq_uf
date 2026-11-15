# profiler2

按批增量更新的字段画像。

- 采集侧 `observe(rows)` 只投递批次，返回后立即继续，画像在后台线程更新，不阻塞采集；
- 每批只保留聚合量：Welford 均值/方差、可合并的 TDigest 分位数、空值计数、受限取值频次，
  不再保留全表，内存与数据量解耦；
- 异常值阈值由分位和倍数共同决定（默认 p01/p99 ± 2×分位距），可按字段配置；
  命中样本落盘保留，支持人工复核；
- 每次批量更新推进版本、写历史快照并可自动保存；支持导出、按版本查询与回放；
- 字段主类型变化时重置该字段统计，历史快照按 `history_limit` 保留若干版本。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

```python
from profiler2 import Profiler

p = Profiler(path="profile.json")          # 每批自动落盘
p.observe([{"amount": 12.0}, {"amount": None}])   # 不阻塞
snap = p.profile("amount")                  # profile / outliers / version / updated_at
p.review("amount", snap["outliers"][0]["id"], "false_positive", "单位错误", "oncall")
p.query("amount", version=3)                # 按版本查询
p.replay(3)                                 # 回放并截断后续版本
p.export()                                  # JSON
p.verify("amount", rows)                    # 全量 vs 增量误差对比
```
