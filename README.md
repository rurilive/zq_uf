# snapdiff

按内容块取指纹的页面快照与变更检测。脚本、样式、广告位和计数器不参与比对；
正文按段落取稳定特征，变化时定位到具体段落并给出前后摘要；历史版本按时间保留。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 用法

```python
from snapdiff import SnapshotStore, diff, snapshot

store = SnapshotStore(retention=2)                    # 至少保留两版
old = snapshot({"html": html_a, "url": site}, store=store)   # 入库，返回版本号
new = snapshot({"html": html_b, "url": site}, store=store)

result = diff(old, new, store=store)
result["changed"], result["changes"], result["version"], result["alert"], result["notify"]
```

- `changes`：逐段变化，含 `type`（modified/added/removed）、`paragraph`、
  `before_summary`、`after_summary`。
- 广告 / 脚本 / 样式 / 计数器变化不产生段落变化，`changed` 为假，不触发告警。
- `store.disable_alerts(site)`：关闭告警，比对结果仍入库。
- `store.enable_notifications(site)`：站点观察满 7 天后才真正自动通知。
- `store.mark_false_positive(site, version)`：标记误报样本，告警撤回且该版本不被清理。
- `store.history(site, since, until)` / `store.as_of(site, when)` / `store.get(site, version)`：取回任意一版。
- `store.prune(site, keep=None, older_than=None)`：清理历史，最近两版和误报样本始终保留，返回被删版本号。
