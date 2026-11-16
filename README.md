# reproenv

安全方向的漏洞复现环境小工具：从报告直接拉起互相隔离的环境，按报告步骤验证，用完清理干净。纯标准库，无外部依赖。

## 入口

- `build(report, base_dir=None, resolver=None, limits=None)`：记录目标版本、依赖、初始数据，为本次复现建独立 workspace（`report.json` 写入 `env_id`，另有 `data/`、`deps/`、`logs/`）。依赖拉取失败停在 `prepare_failed`，资源超限停在 `aborted`，都不进入验证步骤，日志保留失败包名与版本。
- `verify(env, step=None, runner=None, limits=None)`：按顺序跑步骤，返回每步 `results`、整体 `ok`、`failed_step`（卡在哪一步）和清理状态；`step=` 给步骤名或下标可单独重跑某一步。
- `cleanup(env)`：删除该环境 workspace，幂等，不影响其他环境。
- `history(report_id)` / `compare(*envs)`：同一条报告的多次复现放一起比对，结论规范化后判 `consistent`（不含 env_id 等易变字段，同报告重复验证结果一致）。

## 报告字段

`id`、`version`、`deps`（`name==version` 钉死版本）、`steps`（`name`/`expect`/`actual`/`run`）、`initial_data`（或 `data`，`{文件名: 内容}`）、`limits`、`offline`。

## 离线失败与资源护栏

默认 resolver 在本地模拟拉取：报告 `offline: true`、包名以 `missing-`/`fail-`/`nonexistent` 开头、或版本为 `0.0.0` 即拉取失败；也可注入 `resolver=callable` 抛 `DepResolveError` 接管。`limits` 支持 `disk_bytes`（别名 `disk`）、`max_steps`（`steps`）、`timeout_seconds`（`timeout`），超限提前终止。步骤可用 `run=callable(step, env)` 或注入 `runner` 自定义，失败抛 `StepError`。

## 测试与演示

`python3 -m unittest discover -s tests`（24 个用例）；`python3 demo.py` 跑通成功、依赖失败、单步重跑、比对与清理全链路。
