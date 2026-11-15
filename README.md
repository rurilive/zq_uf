# formstep

现状：每步重新取令牌，步骤状态不保存。

问题：并发共用令牌被判重放，回退后流程重来。

目标：按流程保存令牌和步骤，串行推进，回退重取。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 实现说明（10 行以内）

1. `_flows` 按批次保存各步条件、当前令牌、已消耗令牌与结果集，批次间隔离。
2. `begin(flow, steps)` 开批次发首令牌；`step(flow, data, token)` 成功才推进并换发令牌。
3. 重复提交已消耗令牌：返回首次缓存响应（`replayed=True`），结果集只生成一次。
4. 令牌过期报 `stale_token`；条件缺失/为空报 `missing_condition`/`empty_condition`，
   带失败 `step` 与 `field`，令牌不消耗、已选条件保留，修复后原步骤继续。
5. `back(flow, to)` 作废旧当前步及其后条件，`generation` 递增重取页面、换发带 `-rN` 的新令牌。
6. `export_conditions`/`import_conditions`/`copy_flow` 做纯数据导出、导入与复制，
   导入不覆盖已有批次（`flow_exists`），导入后从原步骤继续。
