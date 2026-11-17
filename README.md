# pcapflow

安全方向的审计日志完整性小工具，纯标准库。每条记录带 `link`（前一条摘要，
跨天即前一天末条摘要）和 `digest`（覆盖 link/seq/day/record），按天分段保留
`day_entry_link` 作衔接值。`append(record)` 返回 `seq/digest/link/day_entry_link`。

`verify(span)` 只校验给定区间（seq、range、按天、按时间戳，可跨天），`limit`
分块、`resume` 断点续算，`verify_day(day)` 单验一天。断链时 `broken_detail`
给出前后两条记录与时间，`impact` 给出受影响范围；`day_entry_link` 被改也报。

记录自带 `algo`，`set_algo`/`register_algo` 后旧记录按原算法验证，`rerun(span)`
单独重跑历史区间。末尾整段被删需 `expect_tail` 传外部保存的末尾摘要才能发现，
这是哈希链固有限制。
