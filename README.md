# subdomain

安全方向的子域枚举收敛小工具：把一轮候选按解析结果整理成入口清单。

## 能力

- **入口归并**：指向同一地址的名字合并成一条入口，`names` 保留全部名字，
  同一条入口里共享地址的名字数量记为别名（`aliases`）。一个名字解析到多个
  地址时按地址连通成组，不会因为地址顺序不同把同一条入口拆开。
- **泛解析单列**：`status="wildcard"` / 探测行 `*.example.com` /
  `load(..., wildcard_ips=[...])` 三种方式确认的泛解析地址，命中的名字不进
  入口清单，只在 `wildcard_records` 里保留原始解析记录，事后可核对是否误判。
  规模推断（同一地址命中很多名字且覆盖绝大多数已解析名字）只是兜底，默认
  门槛 `DEFAULT_WILDCARD_THRESHOLD`，`wildcard_threshold=0` 关闭。
- **无效分开记**：解析失败与超时分别计数（`failed` / `timeout`）。失败一次
  即确认为无效，之后同域名再遇到直接跳过（`skipped`），不重复上报；
  超时不算确认无效，下一轮照常重试。
- **按轮次存放**：同一域名反复 `load` 就是多轮，结果各自保留。`diff(domain)`
  或 `enumerate(domain, only_new=True)` 只给相对之前轮次新增的名字，已确认过
  的记录不会再报一遍。
- **误判可恢复**：`restore(domain, name, ip=None)` 把通配命中或已确认无效的
  记录恢复成有效入口，恢复记录在 `report()["restored"]` 里可查；没有地址
  可用的失败记录只解除无效判定，等下一轮重新解析，不凭空造入口。

## 用法

```python
from subdomain import load, enumerate, report, diff, restore

load("example.com", rows)               # 第一轮候选
load("example.com", rows2)              # 第二轮的候选 = 第 2 轮
out = enumerate("example.com")          # 最新一轮：entries/total/aliases/wildcard/invalid
rep = report("example.com")             # 运营视角：candidates/valid/coverage/...
new = diff("example.com")               # 两轮之间只比新增部分
restore("example.com", "rand.example.com", ip="10.0.0.7")   # 误判单独恢复
```

`enumerate(domain)` 与 `report(domain)` 的调用方式与旧版一致，返回值在原有
字段上补充 `entries[].names`、`wildcard_records`、`failed`/`timeout`/`skipped`
与 `diff` 等字段。`report()` 的 `valid` 是归并后的真实入口数，不再等于候选
条数；`coverage` 是有效入口占本轮出现过的名字数的比例（含本轮跳过的已确认
无效项，所以反复枚举不会让覆盖率虚高）。`load()` 返回值里的 `skipped` 列出
本轮再次出现、已确认无效的名字，调用方据此省掉重复解析。

运行 `python demo.py` 看完整演示，`python -m unittest discover -s tests` 跑测试。
