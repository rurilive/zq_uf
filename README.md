# dnscache2

现状：每次解析都返回固定地址，不缓存也不看健康度。

问题：解析慢，结果不过期，坏地址仍被优先选中。

目标：带TTL和健康度的缓存，失败降权，机房变更定向刷新。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

实现摘要（`dnscache2/dns.py`）：

- `DnsCache` 按站点缓存多地址，TTL 内复用；过期后台刷新，刷新期间旧地址继续可用，完成后整体替换地址表。
- 刷新失败保留旧结果、告警并按短间隔重试；首次加载无旧结果时抛 `DnsResolveError`。
- 每次失败扣健康度，连续失败达阈值进入指数退避冷却，冷却中不参与选择；成功上报逐步恢复。
- 选择规则：过滤冷却地址，健康度降序、RTT 升序；全部冷却时强制刷新，仍无地址抛 `NoAvailableAddress`。
- `begin_idc_switch` 返回 `Rollout`：只刷新灰度站点，`verify()` 通过后 `expand()` 才扩大，失败抛 `GrayVerificationError`。
- `resolve` 返回选中地址、剩余 TTL、健康度与本次是否刷新；时钟/执行器/resolver/告警均可注入。
