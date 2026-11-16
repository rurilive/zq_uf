# idormatrix

沙箱报告小工具：把几十页报告里的关键行为摊平、归类、合并，按样本汇总。

## 解决的问题

- 网络、文件、进程、注册表行为分别提取，同一行为多次出现合并为一条并记录次数；
- 行为按首次出现的秒数排序，关键行为（连接、注入、加密、自启动注册表等）标出 `key` 和发生秒数；
- 异常行为单独列出：内置规则命中（C2 端口、勒索加密、IFEO 劫持等）或报告原文显式标记均计入；
- 静态特征与行为结论放在同一份结果里，对不上的样本通过 `static_mismatches()` 单独列出；
- 同一批样本报告格式不同也能解析；不认识的格式抛 `ReportParseError` 并保留原文，批处理时留档不中断；
- 支持批处理合并查看与按行为类型筛选。

## 支持的报告格式

1. dict 行为列表：`{"id", "behaviors": [{"type", "action", "seconds"}], "static"}`
2. dict 分节：`{"id", "sections": {"network": [...], "file": [...], ...}, "static"}`
3. 文本报告：`样本 ID:` 开头，`[网络行为]/[文件行为]/[进程行为]/[注册表行为]/[静态特征]` 分节，行为行带 `[mm:ss]` 时间戳，行尾 `[异常]`/`[可疑]` 为显式标记

## 入口

```python
from sandboxreport import parse, parse_batch, summary, merge_samples, types

parse(report)
# {
#   "id", "format",
#   "behaviors": [{"type", "action", "count", "first_seconds",
#                  "last_seconds", "seconds", "key", "anomaly",
#                  "anomaly_rules", "anomaly_reasons"}],
#   "static": {...},            # 静态特征，与行为结论放在一起
#   "anomalies": [...],         # 异常行为单独标出
#   "static_mismatches": [...], # 静态特征与行为对不上的类型
# }

summary("s-001")                          # 按样本汇总
summary("s-001", behavior_type="network") # 按行为类型筛选
parse_batch([report1, report2, ...])      # 批处理，坏格式进 failed 并保留原文
merge_samples(behavior_type="file")       # 合并查看整批样本
types()                                   # 已出现的行为类型
```

## 行为条目字段

| 字段 | 含义 |
| --- | --- |
| `count` | 同一 (类型, 动作) 出现次数 |
| `first_seconds` / `last_seconds` | 最早 / 最晚出现秒数 |
| `seconds` | 每次出现的秒数（保留原始顺序） |
| `key` | 关键行为，需重点看发生秒数 |
| `anomaly` / `anomaly_rules` / `anomaly_reasons` | 异常标记、命中的规则 ID 与中文说明 |

## 运行

```bash
python3 -I -m unittest discover -s tests -t .   # 测试（27 个用例）
python3 -I -c "import runpy,sys; sys.path.insert(0,'.'); runpy.run_path('demo.py', run_name='__main__')"
```
