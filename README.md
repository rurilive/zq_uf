# dnscache2

现状：每次解析都返回固定地址，不缓存也不看健康度。

问题：解析慢，结果不过期，坏地址仍被优先选中。

目标：带TTL和健康度的缓存，失败降权，机房变更定向刷新。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
