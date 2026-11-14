# jobpriority

现状：单队列先进先出。

问题：大租户独占，高优排不到，抢占丢进度。

目标：配额加优先级，保底，抢占续跑和统计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
