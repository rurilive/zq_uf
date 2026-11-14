# proxyrotate

采集出口轮换模块，纯 Python 标准库。支持会话绑定、失败轮换、
冷却回切和站点隔离，选路确定性（按出口池顺序，无随机）。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口

`acquire(session)` / `release(session, proxy, ok)` 签名保持不变，
两种用法：

- 模块级函数：操作默认站点实例，老代码无需改动。
- `ProxyRotator` 实例：每个站点一个，或用
  `get_rotator(site, **kwargs)` 按站点名获取/复用。

```python
from proxyrotate import ProxyRotator

rotator = ProxyRotator(
    site="shop",
    failure_threshold=3,  # 连续失败 N 次进冷却，可配
    cooldown=60.0,        # 冷却秒数，可配
    session_ttl=300.0,    # 会话绑定有效期秒数，可配
)
proxy = rotator.acquire("session-1")
rotator.release("session-1", proxy, ok=False)
```

## 口径

1. **会话绑定**：会话在 `session_ttl` 有效期内固定同一出口，
   跨请求不换，登录态不丢。仅绑定到期且原出口仍健康时静默续期，
   首次绑定不算切换、不写记录。
2. **失败轮换**：`release(..., ok=False)` 按出口累计连续失败数，
   达到 `failure_threshold` 后出口进入冷却，绑定会话立即解绑，
   下一次 `acquire` 换到健康出口，记录原因 `failure_threshold`；
   成功上报清零失败数并解除冷却。冷却中的出口不会被任何会话选中。
3. **冷却回切**：原出口冷却结束、且会话当前绑定也到期后，优先
   回到最近用过的历史出口，并写**显式回切记录**，原因
   `cooldown_elapsed`；冷却未结束或绑定未到期都不提前回切。
4. **兜底**：所有出口都在冷却时，取冷却最早结束的出口，原因
   `all_proxies_cooldown`。
5. **站点隔离**：每个 `ProxyRotator` 实例（或站点名）持有独立的
   绑定、冷却和记录，同一会话标识在不同站点互不影响。

## 状态与记录输出

- `bindings()`：当前有效绑定 `{session: proxy}`
- `cooldowns()`：冷却中出口及解冻时间
- `rotations()` / `switchbacks()`：轮换 / 回切记录（含时间、
  站点、会话、前后出口、原因）
- `last_switch_reason(session=None)`：最近一次切换原因，可按会话过滤
- `proxies()`：出口池副本

测试通过可注入的假时钟驱动，不 sleep、不依赖随机，断言可复现。
