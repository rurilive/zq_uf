# linksieve

现状：整串URL取哈希，首次到期后按固定间隔重访。

问题：追踪参数一变就当新链接，同页变体撑大队列，热点页重访太慢。

目标：按内容指纹判重，合并别名，按抓取历史调整重访间隔。

实现要点：

1. `fingerprint(url)` 先归一化：丢弃追踪参数与页尾锚点，参数排序，主机名小写；路径、路径大小写与分页参数（`page`/`offset`/`cursor` 等）保留。
2. 每个站点一个 `LinkStore`；同一目标只保留最早入队的地址，其余写法记为别名，不会再次到期。
3. `due(url, now)` 的返回新增 `matched_rule`、`explanation`、`canonical_url`、`alias_of`。
4. `observe(url, now, changed)` 记录内容是否更新，常变页面间隔缩短、冷页面拉长（有上下界）。
5. `alias_report` / `alerts` / `export_aliases` 查看并导出别名样本；`rejudge` 用新规则重判历史链接。
6. `start_trial(site)` 单站试运行并给出与旧行为对比的按日指标；`promote` 扩大、`rollback` 只回退该站点。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
