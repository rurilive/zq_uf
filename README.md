# sessionpin2

现状：会话每次绑定到池里的第一个出口，没有有效期。

问题：换 IP 触发风控，出口失效会话全挂，绑定长期占用。

目标：有效期内固定出口，失效重绑，冷却后可回切。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
