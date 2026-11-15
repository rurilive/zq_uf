# crawlverify

现状：只检查必填字段是否存在，缺失就隔离该行。

问题：值错位发现不了，回退整批，人工复核成本高。

目标：字段级核对，坏行隔离，局部回退并按比例抽样。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
