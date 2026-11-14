# sessionpin

现状：会话绑定只存站点和出口，全局一份。

问题：无过期，失效不重绑，站点不隔离。

目标：带过期的绑定，失效重绑，冷却回切和上限。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
