# formbatch

现状：每次调用把所有表单重新提交一遍，成功项也会重提。

问题：一批失败整批重来，没有限速，结果和请求对不上。

目标：幂等批量提交，失败项单独重试，按站点限速。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
