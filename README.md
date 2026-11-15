# detectsoft

采集页面软 404 判定：状态、内容、模板三个维度一起判，纯标准库。

## 判定规则

- 状态码非 200 → 立即判 `soft404`（标记 `bad_status`）。
- 200 但正文为空 / 命中占位文案 / 跳转异常（跳回首页、错误页或跳转链过长）
  → 分别标记 `empty_body` / `placeholder_text` / `abnormal_redirect`，单页不升级。
- 同一 `(site, template)` 连续命中内容信号满 `escalate_after` 页（默认 3）
  → 升级为 `soft404` 并追加 `template_pattern`；此后该模板命中即升级。
  正常页面会重置连续计数。

`judge(page)` 返回 `kind`、`marks`、`evidence`（命中原因）和 `samples`
（命中样本：原始 URL、响应头、正文摘要，供人工复核定位）。
`report(site)` 返回总计；`report(site, by="day")` 按天下钻，
`report(site, by="template")` 按模板下钻（含比例和样本）。

## 阈值与回退

`configure(site, **阈值)` 按站点覆盖（`site=None` 改全局），
`load_config(path)` 从 JSON 加载。可配项：`min_text_len`、
`placeholder_phrases`、`redirect_max`、`redirect_bad_patterns`、
`escalate_after`、`allow_templates`、`allow_url_prefixes`、
`sample_text_len`、`max_samples`。误判用白名单回退，不改代码：

```python
configure("shop", escalate_after=5, allow_templates=["sparse-list"])
```

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
