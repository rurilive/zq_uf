# dnscache2

带 TTL 和健康度的采集出口解析缓存，替代「每次请求都走系统解析」。

## 能力

- 按站点缓存解析结果：TTL 内复用；过期后先返回旧地址（stale-while-revalidate），
  线程池后台刷新，刷新完成再整体切换，同站点刷新单飞防击穿。
- 地址按健康度排序：连续失败乘性降权并指数退避冷却，冷却中不参与候选；
  冷却到期半开放行，成功后按步长慢慢恢复；全部冷却时抛异常而非选坏地址。
- 刷新失败保留旧结果并告警（`Alerter`），冷路径无旧结果才抛 `ResolutionError`。
- 机房变化按站点名/机房名定向失效，只刷新受影响站点；`RolloutManager` 支持
  灰度站点先验证（新代数 + 健康度达标 + 不在冷却），确认后再扩大范围，可回退。
- 返回值含选中地址、剩余 TTL、健康度、本次是否刷新、是否 stale 及全量候选排序。

## 结构

| 文件 | 职责 |
| --- | --- |
| `dnscache2/cache.py` | 缓存、TTL、后台刷新、单飞、结果组装 |
| `dnscache2/health.py` | 健康度、降权冷却、恢复、候选排序 |
| `dnscache2/rollout.py` | 机房切换灰度/确认/回退 |
| `dnscache2/resolver.py` | 后端解析抽象（生产替换为真实 DNS 客户端） |
| `dnscache2/executor.py` | 线程池/同步/手动三种刷新执行器 |
| `dnscache2/alerter.py` | 刷新失败与灰度阻断告警 |

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
