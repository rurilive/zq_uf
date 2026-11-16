# reproenv

安全方向的漏洞复现环境小工具，用报告里的版本、依赖、初始数据和步骤拉起隔离环境。

- `build(report)`：创建独立工作目录并执行准备阶段；记录目标版本、依赖、初始数据，环境标识写进报告 manifest。依赖拉取失败停在 `prepare_failed`，不进入验证，`logs/prepare.log` 保留失败包名和版本。
- `verify(env)`：逐个执行验证步骤，返回每步 `expect/actual/ok`、整体结果与 `failed_step`；`only=` 或 `rerun_step()` 可单独重跑某一步；步骤数、工作目录体积、单步耗时超限时提前终止。
- `cleanup(env)`：删除该环境工作目录，幂等，环境之间互不影响。
- `compare(*outputs)`：同一条报告多次复现的结果并排比对，默认模拟器确定性执行，重复验证结果一致。

无外部依赖，仅用标准库；跑测试：`python3 -m unittest discover -s tests -v`。
