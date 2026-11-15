# crawlmerge2

采集结果的多版本合并模块，纯 Python 标准库。接口保持 `merge(batches)` 与 `report()`，
另提供 `configure` / `replay` / `export` / `reset`。

## 行与批次的口径

每个批次是一个字典：`{"batch": 批次名, "time": 批次时间(可选), "rows": [...]}`。
每行在原有业务字段之外，使用以下元数据字段（其余字段视为业务负载，用于内容比对）：

| 字段 | 含义 | 缺失处理 |
| --- | --- | --- |
| `key` | 合并分组键 | 不参与合并分组的特殊处理，按原值保留 |
| `batch` | 批次标识，取自批次级 `batch`，缺省为批次序号 | — |
| `version` | 版本号，数字或可转数字的字符串 | 按基线版本 **0** 补齐，并在结果中标记 `version_filled: true` |
| `source` | 来源标识 | 允许缺失；未配置优先级的来源同档，且排在所有已配置来源之后 |
| `time` | 行时间，epoch 数字或 ISO 字符串；缺省取批次级 `time` | 回落到批次到达顺序 |

## 合并判定规则

同 `key` 的行分为一组，逐级比较：

1. **版本高者胜**。缺版本行补齐为 0，因此任何显式版本都高于缺版本行；
   缺版本行之间的相对次序由第 2 级（来源优先级）补齐判定。
2. **同版本比来源优先级**。来源优先级可配置（见下），列表中越靠前优先级越高；
   未配置的来源同档且殿后。
3. **同来源不同批次按时间排序**，时间新者胜；没有可用时间时按批次到达顺序，后到者胜。
4. **冲突**：同版本、来源优先级同档、来源不同且业务内容不同。冲突不丢数据——
   两侧（及所有同档行）的**原始行完整保留**在 `report()["conflicts"]` 中，
   合并结果里仍按时间/批次顺序确定性选一个赢家，判定标记为 `conflict`。
   内容完全相同的行视为 `identical_duplicate` 丢弃，不算冲突。

## 来源优先级配置

```python
from crawlmerge2 import configure, merge

configure(["official", "mirror"])          # 模块级默认配置
merge(batches)                              # 使用默认配置
merge(batches, source_priority=["a", "b"])  # 仅本次合并生效，并写入回放快照
```

## 产物、回放与导出

`merge()` 返回 `{"merged": 行数, "conflicts": [...], "dropped": [...], "merged_rows": [...]}`，
每行带批次、版本、来源与判定（`version_win` / `source_priority_win` / `time_win` /
`batch_order_win` / `identical` / `conflict` / `sole`），丢弃行附原因。
`report()` 给出最近一次合并的逐行来源与判定、冲突和丢弃明细。

```python
from crawlmerge2 import merge, report, replay, export

merge(batches, source_priority=["official", "mirror"])
replay()          # 对保存的输入快照按同一规则重算，结果与首次 merge 完全一致
export()          # 返回 JSON 字符串（含配置、输入快照与全部产物）
export("out.json")  # 写文件；replay("out.json") 可跨进程回放
```

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
