# sessionpin

会话出口粘性：带过期的会话绑定，站点隔离，按站点上限。纯标准库。

接口：`bind(session, site)` / `rebind(session, reason)`，另有 `configure(site, ...)` 和 `records()`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

有效期：绑定默认 300 秒（`configure(site, ttl=...)` 可按站点改）。同一会话在同一站点、
有效期内固定出口；过期后下一次 `bind` 才重绑，过期不算出口故障、旧出口不进冷却。

重绑口径：出口失效时调 `rebind(session, reason)`，记录原因和旧出口，旧出口进入冷却
（默认 60 秒，`cooldown=` 可配），冷却内不再分配，冷却结束后允许回切（记录为 switchback）。
站点之间绑定互相隔离；每站点绑定数有上限（`max_bindings=`，默认 128），超限拒绝新会话
（抛 `BindingLimitError`）。`records()` 输出全部绑定、重绑和回切记录。
