# reportcheck

安全方向的配置基线比对小工具。

- `baseline(config, tolerances=None)` 登记基线和每项允许的差异范围（数值容差 / 枚举 / {"min","max"} 区间）。
- `diff(host)` 按基线逐项比对，返回漂移项（配置项、主机、基线值、当前值、重复次数）；整改后重新比对与基线一致才算通过。
- `report(key=None, host=None)` 按配置项和主机两个维度统计漂移项与整改状态，重复漂移单独计数，事件历史可按时间回看。
- `set_tolerance(key, spec, confirm=None)` 管理员调整允许范围，需带 token 二次确认才生效。

运行测试：`python3 -m unittest discover -s tests`
