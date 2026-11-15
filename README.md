# crawlprio2

现状：按入队顺序出队，优先级只记录。

问题：大站挤占小站，高优无限插队，等待久了不变化。

目标：按站点配额出队，等待提权，高优插队消耗配额。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
