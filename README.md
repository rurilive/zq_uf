# taskfair

现状：全局先进先出，队列名只做标记。

问题：大租户独占，低优饿死，抢占丢进度。

目标：按权重轮转，保底配额，抢占续跑和等待统计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
