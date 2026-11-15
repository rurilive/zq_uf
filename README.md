# pagecore

按块打分的正文抽取：按内容密度和链接比例区分正文与噪声（导航/评论区/推荐位），
短正文保留并给较低把握程度，规则集中在 SiteConfig 按站点覆盖，改版只改配置。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

- `extract(html, config=None)` → `{text, chars, blocks, kept, noise, short, confidence}`，每块带 score/link_ratio/kept/reason
- `blocks(html)` → 块列表，定位哪些算正文、哪些被剔除及原因
- `SiteConfig` → 站点规则（noise_keywords、keep_ratio 等），`extract(html, config={...})` 覆盖
- `Extractor` → 抽取失败返回上次结果（`stale=True`）且不覆盖；`diff_results(old, new)` 对比新旧正文段落
- 改版流程：抽样 `diff_results` 对比新旧正文 → 确认没丢段落再全量切换；旧正文保留一周，便于对比和回退
