# sitemapdiff

现状：比较链接集合和最大深度。

问题：只看链接集合，栏目层级变化漏掉，没有样本。

目标：结构指纹，栏目与层级变化，样本和连续升级。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

**结构指纹**：`snapshot(sitemap)` 按栏目（默认路径第 1 段）、深度和链接数生成
`fingerprint.digest` 与逐栏目 `sections[栏目].digest`；协议、域名、查询串、
`index.html` 不参与，重复链接与抓取顺序归一。`diff(old, new)` 返回
`fingerprint`、`changes`（逐栏目新旧对比）、`samples`（按栏目抽样）和
`alerts`。栏目名可配 `section_level`，无路径的链接归入 `(root)`。

**告警与阈值**：`new_section`（新栏目，`new_section_min_links`）、
`level_change`（栏目换父级或深度层消失，`level_change_min_links`）、
`bulk_removal`（删除数 ≥ `bulk_removal_min` 且比例 ≥ `bulk_removal_ratio`）。
同一告警连续 `escalate_after` 批后由 `warning` 升为 `alert`，中断即清零；
跨批累计用 `Tracker(config).diff`。样本每栏目最多 `sample_size` 条。
