# blockvote

现状：只看403，命中就永久停用站点。

问题：一次误判停整站，多信号互相矛盾，停用后不恢复。

目标：多信号加权投票，阈值熔断，低频探针恢复和带期白名单。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
