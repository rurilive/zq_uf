# failclass

现状：所有失败都记成 error，一律重试三次。

问题：永久失败也重试，统计分不开，站点级故障被放大。

目标：按超时、封禁、不存在分类，预算按站点共享。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
