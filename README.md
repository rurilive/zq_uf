# pagecore

采集页面正文抽取：按内容密度和链接比例给块打分，正文与噪声分开；短文保留并降低把握程度；规则按站点覆盖，改版只改配置。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口

- `extract(html, site=None, overrides=None)`：`text` 正文、`kept` 保留块、`noise` 噪声块、`dropped` 剔除块（均带 `index/locator/score/reason`）、`confidence` 把握程度、`short`/`fallback`/`page_type`。
- `blocks(html, site=None, overrides=None)`：全部块，`kind` 为 `content`/`noise`，`keep` 标记是否进正文。
- `set_site_config(domain, **rules)`：站点规则增量覆盖（如 `noise_hints=["partnerbox"]`），可 `save_site_config`/`load_site_config` 版本化。
- `PageStore(root)`：`extract_safe` 失败保留上次结果（`stale=True`），`stage`/`diff`/`promote` 抽样对比后切换，`rollback` 回退，历史正文保留 7 天后清理（`retention_days` 可调）。

## 改版切换流程

1. `stage` 抽取候选，不覆盖线上正文；
2. `diff` 看 `missing`，确认没有丢正文段落（细微改写不算丢失）；
3. `promote` 全量切换，丢段落时默认拒绝，人工确认后传 `require_no_missing=False`；
4. 抽取失败时 `extract_safe` 保留上次结果，确认后 `force=True` 覆盖；
5. 旧正文留一周，可 `rollback` 退回。
