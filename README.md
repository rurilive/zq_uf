# sessionaudit

现状：每次使用新建会话，过期会话照样能用。

问题：会话文件堆积，使用记录缺失，查不到账号使用情况。

目标：使用写审计，过期自动失效并安全回收。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
