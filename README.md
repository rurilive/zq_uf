# proxysettle

现状：采集请求不分成功失败，全部累加到出口余额。

问题：失败请求也计费，出口切换后旧费用不退，结算不落盘。

目标：抓取成功且入库才计费，失败退费，按天结算并与入库行数对账。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
