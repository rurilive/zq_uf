# formstep

现状：每步重新取令牌，步骤状态不保存。

问题：并发共用令牌被判重放，回退后流程重来。

目标：按流程保存令牌和步骤，串行推进，回退重取。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

实现：
- 条件按批次逐步保存在 `_flows[flow]["conditions"]`，批次间隔离。
- `step(flow, data, token=...)` 提交当前步；已消费令牌再提交返回首次结果并标 `replayed`，只产一套结果。
- `back(flow, to_step)` 作废其后条件并重取令牌，返回标 `back`。
- `fail=True` 模拟失败：条件存 draft、步骤不动，返回 `failed_step`/`failed_condition`，恢复后原步重提。
- `finish` 后 `export` 导出条件；`import_conditions`/`copy_to` 落到新批次从原步骤继续，已存在批次不覆盖。
