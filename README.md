# pagecore

按块打分的正文抽取，纯 Python 标准库。

## 接口

```python
from pagecore import extract, blocks

extract(html, site=None, rules_version=None, config=None)
blocks(html, site=None, rules_version=None, config=None)
```

`extract` 返回：`text`（正文）、`noise`（噪声块文本）、`scores`（各块分数）、
`confidence`（置信度）、`low_confidence`（短正文/兜底标记）、
`rule`（命中规则：站点 + 版本）、`boundaries`（正文边界：块下标 + 字符偏移）。
`blocks` 返回逐块明细：文本、文本密度、链接密度、标点比例、分数、是否正文。

## 分块与打分

- `nav/article/section/aside/div/ul` 等分节标签切块；`p/span/a` 不切块，文本并入所属块。
- 每块打分：`sqrt(文本长) × 文本密度 × (1 - 链接密度) × (1 + 标点比例)`。
  文本密度 = 文本长 / (文本长 + 10×标签数)；链接密度 = 链接内文本占比。
- 分数 ≥ `min_score` 且链接密度 ≤ `max_link_density` 判为正文块；
  以最高分块为锚向两边扩连续正文块（长文分块不丢尾段），
  相邻够长、链接少、有标点的块按段落补回（窄栏文章逐段拼回）。
- 没有块过线时全量保留，`confidence=0.2` 且 `low_confidence=True`（短正文不丢）；
  正文短于 `short_content_len` 同样给 `low_confidence` 标记。

## 站点覆盖与回退

`DEFAULT_RULES` 是默认阈值；`SITE_RULES[站点]` 是版本列表，最后一版为当前版，
逐项覆盖默认值。站点改版只调配置：

```python
SITE_RULES["narrow.example"] = [
    {"name": "narrow-v1", "min_score": 6.0},
    {"name": "narrow-v2", "min_score": 2.5, "short_content_len": 60},
]
extract(html, site="narrow.example")                # 当前版
extract(html, site="narrow.example", rules_version=0)  # 误抽回退上一版
extract(html, config={"min_score": 3.0})            # 临时覆盖
```

可覆盖项：`min_score`、`max_link_density`、`rescue_min_text`、
`rescue_max_link_density`、`rescue_min_punct`、`short_content_len`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
