# fieldsource

现状：按规则顺序取值，只留最终值，后命中的规则覆盖前面的。

问题：字段来源、原始片段和候选规则全丢，规则改名后历史数据对不上。

目标：字段级来源记录，保留全部候选、优先级、版本和跨页来源。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 来源字段

`extract(page, rules)` 返回 `ExtractResult`，可直接当 `字段 -> 主值` 的 dict 用；
每个字段的完整来源在 `result.sources[field]`：
- `value` / `rule`：主值及其规则（`id`、`name`、`version`）。
- `candidates`：全部命中候选，每条含 `rule`、`value`、`snippet`（原始片段上下文）、
  `position`（`{"key", "start", "end"}`，定位不到为 `None`）、`page`、`priority`。
- 规则 `id` 稳定、改名只动 `name`，`version` 随规则演进，历史数据按 `id`+`version` 对齐。

## 优先级与合并

- 主值取 `priority` 最高的候选；未指定时按规则顺序，后命中覆盖前命中。
- `extract_many(pages, rules)` 跨页提取，候选的 `page` 记录来源页（`page["id"]` 或页序号）；
  `merge(results)` 可手动合并多个单页结果，主值按优先级重新选出。
- `result.to_dict(max_snippet=N, max_candidates=M)` 输出可 JSON 序列化的普通 dict，
  截断片段长度和候选数以控制体积；`result.to_json(...)` 直接出 JSON 字符串。
