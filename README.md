# idormatrix

沙箱报告小工具：把几十页、格式不一的沙箱报告解析成统一的行为条目，
按网络 / 文件 / 进程 / 注册表四类归类，重复行为合并计数并按时间排序，
静态特征与行为结论放在一起并自动对账。

## 入口

```python
from sandboxreport import parse, summary, batch, inconsistent, types
from sandboxreport import UnknownReportFormatError
```

- `parse(report)` — 解析单份报告，返回行为条目、次数、静态特征和异常项；
  同一样本 id 重新解析会覆盖旧结果。
- `summary(sample_id, behavior_type=None)` — 按样本查看结论；
  传入 `network` / `file` / `process` / `registry` 可按类型筛选。
- `batch(sample_ids=None, behavior_type=None)` — 同批样本合并查看，
  同名行为跨样本合并并记录涉及样本，结论可按行为类型汇总。
- `inconsistent()` — 列出静态特征与行为对不上的样本及具体对账条目。
- `types()` — 规范行为类型清单。
- `reset()` — 清空已解析报告（测试用）。

## 行为条目结构

```python
{
    "type": "network",            # network/file/process/registry
    "action": "connect 10.0.0.5:443",
    "count": 2,                   # 同一行为出现次数
    "seconds": [9, 12],           # 各次发生的秒数，已排序
    "is_key": True,               # 关键行为（高信号动作）
    "anomaly": "outbound network connection",  # 异常原因，非异常为 None
}
```

行为按首次发生秒数升序排列；`is_key` 标出的关键行为可直接用
`seconds` 还原时间线。异常行为同时汇总在结果的 `anomalies` 字段里。

## 支持的报告格式

1. **flat**：顶层 `behaviors` 列表，每条带 `type/action/seconds`。
2. **sectioned**：顶层 `sections`，节标题含网络/文件/进程/注册表
   （中英文关键词均可），事件字段支持 `action/detail/op/operation`
   与 `seconds/time/timestamp/at` 别名；不认识的节跳过。
3. **text**：纯文本逐行日志，`[秒数] 类型: 动作`，
   `static: key=value` 行给静态特征，`#` 开头为注释。

格式自动识别。认不出来（或文本行无法解析、缺少样本 id）时抛
`UnknownReportFormatError`，原始报告完整保留在异常的 `raw` 字段里，
不会静默丢失或被猜成别的格式。

## 静态特征对账

只在静态特征给出明确布尔断言时核对，没声明的能力不臆测：

- `network`：声明的联网能力 vs 实际网络行为
- `file_write`：声明的文件写入能力 vs 实际写入/创建/投放
- `persistence`：声明的持久化 vs Run 键 / 自启动目录等行为

不一致条目进 `mismatches`，`consistent` 置为 False，
并可通过 `inconsistent()` 或 `batch()` 单独汇总。
