# sqldetect

安全方向的取证时间线小工具，把文件、日志和注册表里的时间排到一起。

- `timeline(evidence, case=None)`：各来源时间归一到 UTC（原始值保留在 `times`/`records`），
  同一主体/对象/动作且相邻（默认 5 秒窗口）的记录合并成一段并保留全部来源；
  段内偏差超过阈值（默认 5 秒）的并列进 `conflicts`，同时给出原始值和归一值；
  缺失或无法解析时间的记录单独进 `untimed`。
- `report(case=None)`：按案件汇总事件数、来源分布、合并条数、冲突数和最大偏差。
- `by_source(source, case=None)`：按来源筛选事件，便于核对单份证据。

同一 `case` 多次调用 `timeline` 会合并该案件的全部证据。运行 `python3 demo.py` 查看示例。
