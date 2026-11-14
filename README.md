# rowmerge

数据行合并的冲突处理模块，纯 Python 标准库。

原来按到达顺序后到覆盖：旧版本能盖掉新版本，并发修改被静默吞掉，删除和更新交叉时旧行复活。
现在改为**版本向量 + 墓碑**，接口仍是 `merge(rows)` 和 `report()`。

```bash
python3 demo.py                      # 两端并发修改、墓碑、回查、批次审计
python3 -m unittest discover -s tests
```

## 行格式

```python
{
    "id": 1,                                  # 主键，必填
    "version": {"repo": 1, "region-a": 1},    # 版本向量：{来源: 计数}
    "updated_at": 1010,                       # 更新时间（数字或字符串，可缺失）
    "source": "region-a",                     # 来源（可缺失）
    "deleted": False,                         # 墓碑标记，True 表示删除（可缺失）
    "name": "王五", "stock": 12,              # 其余字段都是数据值
}
```

`id`、`version`、`updated_at`、`source`、`deleted` 是元数据，不参与"值是否相同"的比较。

## 合并口径

### 1. 版本向量按分量比较

`version` 是 `{来源: 计数}` 的字典，逐个分量比较，**不看总数**：

| 关系 | 判定 | 结果 |
| --- | --- | --- |
| 逐分量都不小于，且至少一个分量更大 | 严格新版本 | 新版本胜出，旧行记 `superseded_by_newer` 丢弃 |
| 逐分量都相等 | 同版本 | 进入来源优先级仲裁 |
| 互有大小（如 `{a:1}` vs `{b:1}`） | 并发版本 | 两侧都是候选，记 `concurrent` 冲突 |

所以 `{a:2}` 不会被 `{a:1,b:1}` 覆盖（两者并发，不是"总和相同所以相等"），
旧版本也永远不能覆盖新版本。

### 2. 同版本 / 并发版本的仲裁顺序

候选之间用下面这条**确定性**的总序选胜者（先比前面的项，不同就分出胜负）：

1. **墓碑**：`deleted=True` 优先。删除与并发更新相遇时**删除优先**（remove-wins），
   避免旧行复活；这一条排最前，不受来源优先级影响。
2. **来源优先级**：数值越大越优先，见下节；未登记来源按 `0`。
3. **更新时间**：`updated_at` 越大越新；数字 > 字符串 > 缺失。
4. **来源名**：字符串比较，保证平局也有确定结果。
5. **数据值**：值的规范化 JSON 文本比较，最后的兜底。

因为这条总序与到达顺序无关，**同一组输入按任意顺序合并都会收敛到同一行**；
`demo.py` 里把 A、B 两端两种到达顺序各跑一遍验证了这一点。

### 3. 来源优先级

来源优先级是"来源 → 整数"的登记表，数值越大越优先，未登记来源为 `0`：

```python
set_source_priority({"region-a": 10, "region-b": 1})
set_source_priority({"nightly": 100}, replace=True)   # 先清空再登记
merge(rows, source_priority={"region-c": 5})          # 本批次临时覆盖
```

口径：

- 只在**版本向量无法分出胜负**时使用；新版本永远赢旧版本，与优先级无关。
- 优先级高的行即使后到，也仍然覆盖优先级低的同版本行（不会因为"后到"丢失仲裁）。
- 优先级低的同版本行后到时记 `conflict_loser` 丢弃，并保留在冲突记录里可回查。

### 4. 墓碑

删除就是提交一条 `deleted=True` 的行，必须带版本向量，版本向量同样要推进：

- 旧更新（版本被删除版本严格支配）不能复活，记 `tombstoned` 丢弃；
- 并发更新（版本互不包含）与墓碑相遇，删除优先，记 `concurrent` 冲突
  （`resolution: "tombstone"`）；
- 版本严格更新的重建可以复活墓碑，这是显式的重新创建，不算冲突。

### 5. 没有版本信息的老数据

整批输入以及已存状态都没有 `version` 字段时，保持原来的"后到覆盖"语义，
不产生冲突和丢弃记录，兼容旧调用。

## 返回值

`merge(rows, source_priority=None, batch=None)` 返回本批次的结果：

```python
{
    "rows": {1: {...}, 2: {...}},   # 全部主键的收敛行，含墓碑行
    "conflicts": [ {...}, ... ],    # 本批次冲突
    "discards": [ {...}, ... ],     # 本批次丢弃
    "batch": 1,                     # 批次号，或传入的 batch 标识
}
```

冲突记录保留**两侧的值**：

```python
{
    "key": 1,
    "batch": "A->hub",
    "reason": "concurrent",         # concurrent | same_version
    "resolution": "source_priority",# tombstone | source_priority | updated_at | source_name | payload
    "winner": {"source": ..., "version": ..., "updated_at": ..., "deleted": ..., "row": {...}},
    "sides":  [ ...两侧摘要，按仲裁顺序从高到低... ],
    "values": [ ...两侧完整行... ],
}
```

丢弃记录说明为什么丢：

```python
{
    "key": 1,
    "batch": "A 旧值重放",
    "reason": "tombstoned",  # superseded_by_newer | tombstoned | conflict_loser | duplicate
    "source": "region-a",
    "version": {"repo": 1, "region-a": 1},
    "row": {...},            # 被丢弃的完整行
    "winner": {...},         # 胜者摘要
}
```

只记录**本批次提交却被拒绝**的行；被替换的旧状态属于正常演进，不算丢弃。

## 回查与批次审计

```python
report(key=1)        # 按主键回查冲突；report() 返回全部
discards(key=1)      # 按主键回查丢弃
report(batch="A->hub") / discards(batch="A->hub")   # 按批次过滤
lookup(1)            # 当前行 + 冲突 + 丢弃 + 涉及批次
audit()              # 全部批次归档；audit(batch="A->hub") 取单批
```

`audit()` 每个批次一条：

```python
{
    "batch": "A->hub", "seq": 2, "at": 1720000000.0, "input": 1,
    "keys": [1], "conflicts": [...], "discards": [...],
    "outcome": {1: {"source": ..., "version": ..., "deleted": False}},
}
```

返回的都是深拷贝，改返回值不会污染内部状态。

## 其他接口

- `reset(include_priority=False)`：清空合并状态、归档和批次序号（测试用）。
- `set_source_priority(mapping=None, *, replace=False)`：登记 / 查看来源优先级。

## 已知取舍

- 版本向量只按"行"维护，不做字段级合并，冲突时整行取一侧胜者，另一侧完整保留在冲突记录里。
- 状态是进程内的，不持久化；批次号从 1 自增，跨进程需自行传入 `batch`。
- 墓碑行一直留在 `rows` 里，由调用方按 `deleted` 过滤；不做物理清理。
