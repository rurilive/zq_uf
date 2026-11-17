# vulnretest

安全方向的漏洞复测记录工具，纯 Python 标准库。

- `retest(vuln)` 跑一轮复测：沿用原验证步骤，与修复前基线（首轮基线可由 `vuln["baseline"]` 给出）逐项比对，返回 `result`、`diff`（与上一轮的 fixed/regressed/unchanged/new/removed）、`failures`（步骤、原因、当前证据、修复前证据）和 `closed`。
- 步骤名称或顺序变化时自动重新生成基线，旧记录保留当时使用的基线；历史记录各自保存修复前证据。
- 通过后工单自动关闭，未通过保持原状；关闭后再次复现按新问题处理，新工单为 `<原工单>-R<n>` 并通过 `linked_ticket` 关联原工单。
- `history(vuln=None, project=None)` 按漏洞或项目查看记录（按时间排列），`vulns` 给出每个漏洞的最新结论，`projects` 给出项目汇总；`history(project=...)` 即按项目筛选。

运行 `python demo.py` 看两段演示，`python3 -m unittest discover -s tests` 跑测试。
