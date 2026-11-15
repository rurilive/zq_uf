# rateadapt

现状：所有站点固定速率，结果只记录。

问题：慢站被打挂，429 后不降速，突发绕过限速。

目标：按成功率和 429 自适应，慢涨快降，与并发共用预算。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
