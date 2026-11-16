# vulnretest

漏洞修复后的复测记录工具。复测沿用原验证步骤，并把本轮结果与修复前基线
逐项比对：全部通过才关闭工单，未通过记录失败点和当前证据。多次复测按时间
排列，可按漏洞或项目查看。

```python
retest({"id": "V-7", "project": "shop", "ticket": "SEC-1201",
        "baseline": {"at": "...", "steps": [{"name": "回显", "ok": False}]},
        "steps": [{"name": "回显", "ok": True, "evidence": "http://new"}]})
history("V-7")            # 单个漏洞的复测记录
history(project="shop")   # 按项目筛选并汇总
```

`retest` 返回 `result`（`pass`/`fail`/`baseline`）、`diff`、`failures`、
`since_last`、`evidence` 和 `ticket` 状态。首次复测或步骤变化后先重建基线，
该轮为 `baseline`，不判通过；关闭后再次复现按新问题处理并关联原工单。
