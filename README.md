# dnscache2

采集出口 DNS 缓存：按站点缓存解析结果，按地址可用程度选路，机房切换可灰度。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 行为

- `resolve(host)` 复用缓存（默认 TTL 30s）；过期后先返回旧地址，后台刷新，下次调用才切换。
- 地址按健康分降序、延迟升序排序；连续失败降权并递增冷却（5s 起，上限 300s），
  冷却中的地址不会被选中；成功后健康分立即回升，惩罚随时间（约 60s）指数衰减。
- 刷新失败保留旧地址并告警，按 5s 起、上限 300s 的间隔退避重试。
- `switch_datacenter(dc, canary=..., probe=...)` 先在灰度站点验证新地址，
  通过后再刷新其余受影响站点；验证失败则整体不切换，旧结果继续可用。
- `report(host, ip, ok, rtt)` 上报结果；`rank(host)` 查看排序；`clear(dc)` 定向清除。
