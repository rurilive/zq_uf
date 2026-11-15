# pagefinger

纯 Python 标准库的页面指纹与近重复判定模块。不依赖第三方包。

## 解决的问题

旧版对整页文本直接取 SHA256：广告或脚本一改指纹就变、相似页面判不出来、
阈值写死导致短页面误判。新版改为**正文分块指纹 + 块重合率相似度**。

## 接口

```python
from pagefinger import fingerprint, similar

fingerprint(html)          # 单页指纹
similar(a, b)              # 两页近重复判定
similar(a, b, site="news.example.com")          # 按站点取阈值
similar(a, b, threshold=0.9)                    # 调用方显式阈值
```

## 噪声剔除口径

提取正文前整体跳过，**其内容不进入页面 hash 也不进入任何块**：

- 标签：`script`、`style`、`noscript`、`template`、`iframe`、`embed`、`object`；
- 广告块（默认规则，命中任一即剔除）：
  - 标签为 `ins`（AdSense 插入位）；
  - `id`/`class` 令牌命中 `ad`、`ads`、advert、adsense、sponsored、promo、
    banner、`ggad` 等（按空格/下划线/连字符拆词后精确匹配，避免误杀
    "header" 这类包含 "ad" 的正常词）；
  - `src`/`data-src` 含 doubleclick、googlesyndication、adsystem、adserver 等投放域名。
- 规则可扩展：`register_ad_rule(func)` 注册 `func(tag, attrs) -> bool`，
  任一规则返回真即剔除；`unregister_ad_rule(func)` 注销。

## 分块与归一化口径

- 按块级标签（`p`、`div`、`section`、`article`、标题、列表、表格等）切段；
- 归一化后短于 `min_block_chars`（默认 8 字符）的块视为不稳定块（按钮、
  导航词等）丢弃，其余为**稳定块**，各自取 SHA256 块指纹；
- 块指纹归一化：NFKC、CJK 标点转半角、删除 CJK 字符旁空白（拉丁词间
  空格保留）、折叠连续空白、转小写、剔除标点。排版/标点/空白变化不改变块指纹；
- 页面 `hash` 是剔除噪声后的**精确**指纹（保留正文原始空白差异，不做归一化），
  用于"同一页面"判定；块指纹用于"近重复"判定。两级口径分离。

## 指纹版本号与重算

`fingerprint()` 返回 `version`（当前为 1），页面 hash 与块 hash 的输入都带
版本前缀。算法升级时递增 `FINGERPRINT_VERSION`，新旧指纹不会碰撞，历史数据
可按 `version` 字段筛选后批量重算。同一页面多次计算结果完全一致（确定性）。

## 相似度与阈值口径

- 相似度 `score` = 两侧稳定块哈希集合的 Jaccard 重合率 =
  交集块数 / 并集块数，范围 0~1；
- 默认阈值 `threshold=0.75`，可通过 `configure(threshold=...)` 调整全局默认，
  或 `configure_site(域名, threshold=...)` 按站点覆盖；显式 `threshold=` 优先级最高；
- **短页面降阈**：稳定块少于 `short_blocks`（默认 4）或正文字符少于
  `short_chars`（默认 120）即标记 `short=True`。比对时任一侧为短页面，
  阈值自动换成 `short_threshold`（默认 0.5），结果里 `short_page` 单独标注；
- 短页面置信度单独给出：`confidence` 综合块数与字符数（各占一半权重，
  封顶 1.0），比对结果取两侧较低值，短页面必然明显偏低；
- 判定结果保留两侧块样本 `samples` 与命中块样本 `matched_samples`
  （截断到 `sample_chars`、最多 `max_samples` 条），便于人工复核。

## 返回字段

`fingerprint(html)`：`version`、`hash`、`blocks`、`short`、`confidence`、
`block_hashes`、`block_samples`（index/tag/text/hash/chars）、
`stats`（chars、dropped_noise_blocks）。

`similar(a, b)`：`similar`、`score`、`threshold`（实际生效值）、
`short_page`、`confidence`、`matched_blocks`、`block_counts`、
`short_sides`、`samples`、`matched_samples`。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
