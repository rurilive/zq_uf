# proxysticky

采集器的出口管理模块：按站点会话粘滞的出口池，纯 Python 标准库。

## 用法

```python
from proxysticky import StickyPool

pool = StickyPool(PROXIES, sticky_window=300, failure_threshold=3, cooldown=60)
proxy = pool.acquire(site, session)   # 同一会话在窗口内固定同一出口
pool.report_failure(site, session, proxy)  # 连续失败达阈值即摘除并冷却
pool.report_success(site, session, proxy)  # 成功清零连续失败计数
pool.bindings() / pool.switch_log() / pool.status()  # 当前绑定 / 切换原因 / 池状态
```

模块级 `acquire(site, session)` 是兼容入口，共享默认池且 `sticky_window=0`
（不粘滞，保持旧行为）；需要粘性请自建 `StickyPool`。

## 参数与阈值

- `sticky_window`（默认 300s）：会话绑定的粘滞窗口，过期才重选。
- `failure_threshold`（默认 3）：出口连续失败多少次被摘除。
- `cooldown`（默认 60s）：摘除后的冷却时长，冷却结束自动恢复可分配。
- 站点隔离：绑定与故障状态按站点独立，活跃绑定不被其他站点占用。

## 错误

- 空代理表 / 重复代理 / 上报未知出口 / 上报与绑定不符的出口：`PoolError`。
- 站点无可用出口（全部冷却或被占用）：`PoolExhaustedError`。
- 非法参数（负窗口、阈值 < 1、空 site/session）：`ValueError`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
