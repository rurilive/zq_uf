# failclass

现状：所有失败都记成 error，一律重试三次。

问题：永久失败也重试，统计分不开，站点级故障被放大。

目标：按超时、封禁、不存在分类，预算按站点共享。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

实现：
- `classify(error, response)` 归一原因为 timeout / banned / not_found / error，证据附状态码和响应头。
- `policy(reason)` 给出处置：超时等待重试，封禁换出口并冷却，不存在直接放弃。
- `SiteLedger.record(site, task, error, response)` 返回原因、动作、剩余次数、证据和 trace_id。
- 重试预算按站点共享，当天有效（`new_day()` 重置）；封禁冷却到期自动恢复（`site_available`）。
- 放弃的任务进人工复核清单，可按站点导出（`export_review`），复核后 `requeue` 重新入队、不占预算。
- `breakdown(site)` 看失败构成，`records(site, reason)` 追溯到具体请求。
