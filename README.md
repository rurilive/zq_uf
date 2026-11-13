# datasearch

数据目录搜索模块（纯 Python 标准库）：索引表名、描述、字段和标签，支持同义词与前缀匹配，按相关度与热度排序。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 索引字段
- `name` 表名、`desc` 描述、`fields` 字段名、`tags` 标签、`popularity` 热度（≥0 的数）。
- 英文按 `_` 切词并保留原串；中文按内置词表最长匹配切词，未登录字逐字成词。
- 匹配区分大小写；查询按空白分词，多个词需全部命中（AND），查询词本身不再切词。

## 匹配与排序
- 每个查询词在各位置取最优得分后求和：精确命中 > 前缀命中 > 同义词命中。
- 位置权重：表名 4.0 > 标签 3.0 > 字段 2.0 > 描述 1.0。
- 得分 = Σ(位置权重 × 命中系数) + 0.1 × log1p(热度)。
- 排序：得分降序 → 热度降序 → 表名升序，结果确定。

## 参数与阈值
- `MIN_PREFIX_LEN = 2`：词长 ≥ 2 才触发前缀匹配（如 `amo` 命中 `amount`，`a` 不命中）。
- 命中系数：精确 1.0、前缀 0.6、同义词精确 0.8、同义词前缀 0.5。
- 同义词/缩写表见 `datasearch/search.py` 的 `SYNONYMS`（如 订单↔order、成交额↔gmv）。

## 输出、错误与序列化
- `search(query)` 返回命中表（仅 `name`/`desc`/`fields` 三键）；`search_detail(query)` 额外给出得分与命中片段。
- 空查询抛 `ValueError`，非字符串查询抛 `TypeError`；`add_table`/`from_dict` 对非法输入抛明确异常，不静默跳过。
- `SearchIndex.to_dict()`/`from_dict()` 支持 JSON 序列化，重启后搜索结果可复现。
