# sessionpin2

现状：会话每次绑定到池里的第一个出口，没有有效期。

问题：换 IP 触发风控，出口失效会话全挂，绑定长期占用。

目标：有效期内固定出口，失效重绑，冷却后可回切。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

口径：
- `configure(ttl, cooldown, max_bindings, site_limits)` 配置有效期、冷却与上限；`ttl=0` 不过期。
- `bind(session, site)`：同会话同站点在有效期内固定出口，过期才新建绑定；站点之间隔离。
- `rebind(session, reason)`：校验会话仍有有效绑定（否则抛 ValueError），换出口并记录原因和旧出口，旧出口进入冷却。
- 冷却结束后新建绑定优先回切原出口，回切事件记录冷却时间。
- 绑定数超上限（全局 `max_bindings` 或按站点 `site_limits`）按最久未用回收。
- `events()` 按序输出 bind / rebind / switchback 事件。
