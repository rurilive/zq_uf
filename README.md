# crawlmetrics

采集任务统计模块，纯 Python 标准库。指标以可聚合结构保存，快照为 JSON
原生类型，支持分阶段计数、耗时分位数、按站点/任务聚合、跨进程合并以及
重启后继续累加。

## 指标命名

接口保持 `inc(name, value)`，并通过 `site` / `task` 关键字携带维度：

- 分阶段计数器：`<stage>.<result>`
  - `stage`：`request`（请求）、`parse`（解析）、`storage`（入库）
  - `result`：`success`、`fail`、`retry`
  - 例：`stats.inc("request.fail", site="a.com", task="crawl-a")`
  - 分阶段计数器必须带至少一个维度，否则抛 `ValueError`。
- 阶段耗时：`duration.<stage>`，value 单位为秒，也可用等价写法
  `stats.timing("request", 0.12, site=..., task=...)`。
- 不遵循上述约定的名字保持旧行为：在快照顶层按整数累加。

## 基本用法

```python
from crawlmetrics import Stats

stats = Stats()
stats.inc("request.success", site="a.com", task="crawl-a")
stats.inc("request.retry", site="a.com", task="crawl-a")
stats.inc("parse.fail", site="a.com", task="crawl-a")
stats.inc("storage.success", site="a.com", task="crawl-a")
stats.timing("request", 0.12, site="a.com", task="crawl-a")
stats.snapshot()
```

## 快照格式

`snapshot()` 返回深拷贝，只含 dict/list/str/int/float/None，可直接
`json.dumps`。结构如下：

```json
{
  "legacy_metric": 7,
  "counters": {
    "request.success": {
      "total": 1,
      "sites": {"a.com": 1},
      "tasks": {"crawl-a": 1}
    }
  },
  "timers": {
    "request": {
      "count": 1,
      "sum": 0.12,
      "min": 0.12,
      "max": 0.12,
      "mean": 0.12,
      "quantiles": {"p50": 0.125, "p90": 0.125, "p95": 0.125, "p99": 0.125},
      "histogram": {"bins": [0.001, 0.0025, "..."], "counts": [0, 1, "..."]},
      "sites": {"a.com": { /* 同叶子结构 */ }},
      "tasks": {"crawl-a": { /* 同叶子结构 */ }}
    }
  }
}
```

- 顶层除 `counters` / `timers` 外的键是旧风格整数指标；空快照仍为 `{}`。
- 分位数由固定边界直方图（1ms～10s，越界样本计入首/尾桶）估算，
  因此是近似值；直方图桶计数可直接相加，合并后再算分位数。
- 站点/任务维度按出现过的名字滚动聚合，基数即站点数和任务数。

## 跨进程合并与重启

```python
# 多个采集进程各自产生快照，在汇总进程合并
merged = Stats.merge_snapshots([worker_a.snapshot(), worker_b.snapshot()])

# 或在已有实例上累计
stats.merge(another_snapshot)

# 落盘 / 还原，重启后历史指标继续累加
stats.dump("stats.json")
stats = Stats.load("stats.json")
# Stats(snapshot_dict) 同样可以从快照直接构造
```

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
