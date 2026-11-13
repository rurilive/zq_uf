# readclean

纯 Python 标准库的网页正文抽取：解析 -> 剪枝 -> 切块 -> 打分过滤 -> 去重。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 用法

```python
from readclean import extract
result = extract(html, rules=None)  # rules 省略时用 DEFAULT_RULES
# -> {"title", "published", "paragraphs", "confidence"}
```

## 规则字段（extract 的 rules 参数，逐项覆盖默认值）

- `drop_tags`：连同子树删除的标签（script/style/head 等）。
- `drop_sections`：结构性噪声区域标签（nav/footer/aside/header）。
- `drop_keywords`：class/id 命中即删除的关键词（related/comment 等）。
- `min_block_chars`：常规页面保留段落的最少字数，默认 15。
- `short_page_chars`：可见文本低于该值视为短页面，默认 600。
- `short_page_min_chars`：短页面段落最少字数（降阈值防过度裁剪），默认 4。
- `link_density_max`：链接文字占比上限，超过判为导航/推荐，默认 0.5。
- `dedupe_adjacent`：是否合并相邻重复段落，默认 True。
- `date_patterns`：发布时间正则，按顺序尝试，命中即返回。

表格按行转成 `单元格 | 单元格` 文本块保留，不受字数阈值过滤；
置信度 = 保留正文字数 / 页面可见文本总字数（不含 head/script 等）。
