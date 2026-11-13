# quotasched

现状：任务按提交顺序出队，CPU 和内存需求只存在字段里。

问题：大任务独占机器，小任务等几小时，紧急任务没法插队。

目标：按资源装箱调度，高优先级可抢占并保存进度，输出利用率。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
