# changedetect

结构感知的页面变更判定：对齐节点、过滤噪声、字段级差异、分级入库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 对齐规则
- 解析 HTML 为节点树，先剔除 script/style/noscript/template 及广告区域。
- 广告区域：class/id/data-* 分词后命中 ad/ads/advert/adslot/sponsor 等。
- 子节点三轮对齐：稳定属性（id/data-id/data-key/name）→ 标签+全文一致 → 同标签按序。
- 对齐后相对顺序变化的节点记为移动（最长递增子序列之外），未配对记插入/删除。
- 文本按字段归并：同一字段元素的多处文本变化只出一条差异，带旧值、新值、位置。

## 分级口径
- none：无有效变更；layout：仅节点移动；content：仅非业务区域（nav/footer 等）变更。
- business：业务字段（price/title/sku/stock 等 class/id/data-field 标记）或未标记内容变更。
- 未标记内容默认按业务处理（宁可多入库）；只有 business 级入库，其余只记 audit。
