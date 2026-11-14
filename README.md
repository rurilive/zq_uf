# formcaptcha

现状：所有账号共用一个工单队列，按到达顺序处理。

问题：紧急任务排队，超时不回收，回写不确认。

目标：按优先级分派，账号互斥，超时回收和回写确认。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
