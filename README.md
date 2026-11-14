# robotsdiff

现状：规则整份替换，缓存不失效。

问题：不知道哪些链接受影响，旧规则继续放行，变更无审计。

目标：差异分析，定向失效，审计和回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
