# keyleak

安全方向的情报指标归一和匹配小工具，纯标准库。

- 按指标类型分别归一：地址（`ipaddress` 规范化）、域名（小写、去尾点）、样本摘要（md5/sha1/sha256 小写）。
- `normalize(feeds, day=None)`：归一入库，返回归一数量、新增数量、来源分布和过期数量。同一指标保留全部来源及各自可信度，条目有效期取各来源最晚者。
- `match(ioc, day=None)`：匹配单个指标，过期条目不命中；命中时返回按可信度降序的全部来源，冲突来源并列展示。
- `export(day=None)`：导出拦截列表，只含未过期条目，与情报库保持一致。
- `expiring(day, within)`：按天筛选将过期的条目。
- `purge(day=None)` / `cleaned()` / `restore(ioc)`：清理过期条目，清理记录保留，误删可恢复。
- `set_confidence(source, confidence)`：人工调整来源可信度，作用于已有条目和后续归一。
