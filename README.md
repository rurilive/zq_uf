# assetmerge

安全方向的资产合并小工具，把资产表按主机收敛成待下发的扫描清单。

## 解决的问题

同一台主机在资产表里往往有域名、IP、端口多条记录，按记录逐条下发扫描会
反复扫同一台机器、同一漏洞在报告里重复出现、别名入口还会漏掉。本工具按
主机合并后再下发，同一漏洞只留一条并列出全部受影响入口，合错了可以拆回。

## 合并依据

并查集按以下三类依据把记录归入同一主机分组（依据会随分组结果返回）：

- `host`：记录显式标注属于同一台主机（`host` 相同）。
- `resolved-ip`：域名解析到的地址与某条 `ip` 记录相同（解析结果由记录的
  可选字段 `resolves_to` 给出，字符串或字符串列表均可）。
- `port-instance`：`port` 记录 `ip:port` 的地址部分与同地址的 `ip` 记录
  属于同一实例。

合并后主记录取组内 id 最小的记录，其余记录作为别名保留，来源（`source`）
汇总在分组边上。

## 用法

```python
from assetmerge import merge, preview, report, scan_list, split, MergeAbort

# 1. 合并前先预览，拿到会受影响的记录条数（不改变当前状态）
pv = preview(assets)
pv["affected_count"]

# 2. 执行合并；带 expect 时与预览比对，不一致抛 MergeAbort 并列出差异，状态不变
out = merge(assets, expect=pv)
out["count"]          # 原始记录总数（守恒口径，操作前后不变）
out["group_count"]    # 合并后的主机数量
out["alias_count"]    # 别名总数
out["basis"]          # host：发生过主机合并；record：没有任何合并
out["groups"]         # 主记录、别名、来源、合并依据、去重后的漏洞及入口
out["splits"]         # 拆分结果（merge 时为空）

# 3. 按主机下发与汇总
scan_list()           # 每个主机一个主记录 id，按 id 升序
report()              # 主机/资产/别名数量，漏洞分布（同漏洞只算一条）

# 4. 只回退指定的一次合并，其余分组保持原样；资产总数守恒
split(group_id=out["groups"][0]["group_id"])   # 或 split(record_id="a2")
```

`merge(assets)` 与 `report()` 的调用方式与旧版保持一致；`preview`、
`split`、`MergeAbort` 为新增能力。运行 `python3 demo.py` 可看完整演示，
`python3 -m unittest discover -s tests` 跑测试。
