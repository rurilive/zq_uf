# linksieve

按内容指纹判重：追踪参数、参数顺序、页尾锚点不再产生新目标，路径与分页
保留。对外仍是 `fingerprint(url)` 与 `due(url, now)`，返回里带
`due`、`reason`、`rules_hit`、`is_alias`/`alias_of`、`next_due_at` 与
`explanation`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 能力

- `fingerprint(url)` / `due(url, now)`：稳定入口，同一内容的多写法同指纹；
  首个入队地址保留，其余记为别名。
- `LinkStore.due()`：额外给出 `kept_url`、`canonical`、`site`。
- `store.observe(url, digest, now)`：抓取后报告内容摘要；内容常变则缩短
  重访间隔（最短 300s），连续未变则放宽（最长 7 天）。
- `store.alias_alerts()` / `store.export_alias_samples(path)`：别名过多的
  站点告警并导出样本 JSON。
- `store.metrics()` / `store.daily_series(site)`：按站点、按天查看别名比例
  与重复抓取次数。
- `store.start_canary(site, ...)` / `store.promote_site(site)` /
  `store.rollback_site(site)`：单站试运行、转正、只回退该站点。
- `store.set_rules(rules)` / `store.rejudge(...)`：规则更新后重新判定历史
  链接，同键目标合并并保留抓取历史。

## 试运行流程

1. `store.start_canary("站点", tracking_prefixes=(...))`，该站历史链接按新
   规则重判，其他站不受影响。
2. 每天看 `store.daily_series("站点")` 的 `alias_ratio` 与
   `duplicate_fetches`。
3. 指标下降后 `store.promote_site("站点")`；异常则
   `store.rollback_site("站点")`，只把该站恢复为默认规则。
4. 确认稳定后用 `store.set_rules(new_rules)` 推给其他站点。
