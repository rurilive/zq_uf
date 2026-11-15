# crawlprio2

按站点配额的采集调度队列，解决大站挤占小站、高优无限插队、等待不感知三个问题。

## 规则

- **站点轮转**：一批出队中每轮每站最多 1 条，站点间按提权后优先级排序，同一站点不能连续占满名额。
- **等待提权（有上限）**：`提权后优先级 = priority + min(等待秒数 × aging_rate, max_boost)`，入队后动态计算。
- **高优不越配额**：高优任务只在本站队列内插队，出队一律消耗本站当日名额，无独立通道。
- **配额耗尽**：返回 `retry_after_seconds`（到次日 0 点）与各站剩余名额，条目保持原排队；跨天自动重置，运营当天调大立即生效。
- **放弃重入**：`release` 保留首次入队时间与等待资历，`attempt + 1`。
- **租户隔离**：队列、配额、记录按租户完全独立，互不挤占。
- **饥饿告警**：站点连续 `starve_days` 天（默认 3）无出队且仍有待出条目时返回告警，提示运营手动调名额。
- **出队记录**：按租户/站点留存，支持按天查询、JSON/CSV 导出、按天回放重新入队。

## 用法

```python
q = CrawlQueue()                      # clock 可注入，测试/回放用假时钟
q.configure_site("default", "big", 1000)
q.push({"id": "u1", "site": "big", "priority": 9})
q.pop(batch=100)     # items / remaining_quota / retry_after_seconds / starvation_alerts
q.ack("u1")          # 成功；q.release("u1") 放弃重入（保留等待资历，attempt+1）
q.records(site="big", day=...)        # 按天按站查询
q.export_records(fmt="csv")           # 导出；q.replay_day(day) 回放
```

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
