# linksieve

现状：整串URL取哈希，首次到期后按固定间隔重访。

问题：追踪参数一变就当新链接，同页变体撑大队列，热点页重访太慢。

目标：按内容指纹判重，合并别名，按抓取历史调整重访间隔。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

实现：`rules.py` 归一化（去追踪参数、参数排序、去锚点，保留路径与分页）；
`store.py` 按指纹合并目标，最早入队地址为 canonical，其余记别名；
`record_fetch(url, now, changed)` 按更新频率伸缩重访间隔（300s ~ 7 天）；
`due` 返回含是否到期、命中规则、规则版本与原因；
`alias_alerts`/`alias_samples` 告警并导出别名过多站点的样本；
`recanonicalize` 在规则更新后重判历史链接；
`pilot_rules`/`rollback_rules`/`promote_rules` 按站点灰度、回退、推广；
`daily_metrics(site)` 按天查看别名比例与重复抓取次数。
