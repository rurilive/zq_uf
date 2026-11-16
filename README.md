# permaudit

样本库小工具，按内容与相似度判重、归组，并给出家族分布。

## 判重规则

- 完整 digest 相同：只保留最早入库的一份，返回 `duplicate`。digest 相同但内容
  不同（哈希冲突或 digest 填错）不会被静默丢弃，而是以 `digest_conflict` 失败留档。
- digest 不同但内容相似：样本仍入库，方便逐份对比，但归入同一相似组；组内按
  两两相似度总和选出代表（medoid），并列取最早入库者。
- 相似度：4-gram 指纹集合的 Jaccard（bottom-k 采样，插入/删除不会让指纹整体
  错位）。默认阈值 0.85，可用 `set_threshold(family, value)` 按家族调整，调整后
  自动重算该家族的组并列出受影响的组。
- 未填家族时，若与已有样本足够相似则继承其家族（`family_source="inferred"`），
  否则记为 `unlabeled`。

## 接口

```python
from samplededupe import compare, family_view, ingest, report, set_threshold

ingest(sample)                 # 返回新增/归类/相似组/差异/代表
report()                       # 总量、去重前后对比、分组、差异、失败
report(family="ransom")        # 只看一个家族
report(failure_reason="missing_digest")   # 按失败原因筛选
family_view("ransom")          # 家族内的分组与组内差异
compare("a.bin", "a1.bin")     # 逐份对比：大小、相似度、关键差异位置
set_threshold("ransom", 0.9)   # 调整阈值并重算受影响的组
```

`sample` 至少要有 `name` 和 `digest`，可选 `family`、`size` 以及 `content`
（bytes/str）。缺字段或字段非法的样本不会抛异常，而是带 `reason` 留档；
`failures()` / `retry_failed()` 用于筛选与补处理。

## 差异表示

差异给出文件大小、`size_delta` 与关键差异位置 `positions`（`offset`、
`sample_offset`、`length`、`kind`）。小文件用 `difflib` 精确到字节；大文件用
rsync 式块对齐，仍是线性时间，孤立改动逐字节收边后精确到字节。超过上限时
只保留最长的若干处并标记 `truncated`。
