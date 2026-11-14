# siteallow

现状：进程内字典记录封禁站点。

问题：多进程不一致，误封永久，解除无审计。

目标：跨进程共享，到期解除，任务范围和审计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
