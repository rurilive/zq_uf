# keyleak

安全方向的情报指标归一和匹配小工具，纯标准库。

- 按指标类型分别归一：ipv4 / domain / md5 / sha1 / sha256，域名统一小写并去掉结尾的点
- 同一指标保留全部来源和可信度，按可信度排序，冲突来源并列展示
- 指标带有效期，`match` / `export` 按 `day` 过滤，过期自动退出拦截列表
- `normalize(feeds)` 返回归一数量、来源分布和过期数量；`match(ioc)` 返回命中来源
- `cleanup(day)` 清理过期条目并保留记录，`restore()` 可恢复误删
- `set_confidence(source, confidence)` 人工调整来源可信度，传 `None` 恢复原值

入口：`normalize` / `match` / `export` / `cleanup` / `restore` / `set_confidence`。
