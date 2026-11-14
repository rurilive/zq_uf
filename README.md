# crawlbackoff

现状：每个任务各自退避，站点状态不共享。

问题：一个任务被限速其他照打，恢复不一致，回满速再触发。

目标：按站点共享窗口，阶梯恢复，跨进程同步。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
