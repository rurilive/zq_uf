# linkgraph

站点链接图的环路分析：归一化建图，迭代识别自环、二元环和强连通分量。
纯 Python 标准库，接口为 `build(pages)` 和 `cycles(limit)`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 归一化口径
- scheme 和 host 统一小写，query 和 fragment 直接丢弃。
- 路径尾部斜杠去掉（根路径保留 `/`），参数变体合并为同一节点。
- 纯 query/fragment 的相对引用归到来源页。
- 归一化后按键去重，重复出链只算一条边、只报一次环。

## 截断口径
- `cycles(limit)` 的 limit 是遍历节点上限，必须是不小于 1 的整数。
- 超过上限即停止分析，返回已发现的环，`truncated` 置 True。
- 不传 limit 时 `cycles()` 返回 `[]`（兼容旧接口）。
- 报告含 `nodes`、`edges`、`cycles`（path + nodes）、`truncated`。
