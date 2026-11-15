# sessionpin2

现状：会话每次绑定到池里的第一个出口，没有有效期。

问题：换 IP 触发风控，出口失效会话全挂，绑定长期占用。

目标：有效期内固定出口，失效重绑，冷却后可回切。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

用法（10 行以内）：
- `bind(session, site)`：TTL 内固定出口，到期或出口失效自动换绑，返回当前出口与剩余时间
- `rebind(session, site, reason)`：手动换绑，记录原因和旧出口，旧出口进入冷却
- 冷却结束后由下一次换绑触发回切；冷却中返回里带提示和冷却结束时间
- 绑定按 `(session, site)` 隔离，上限 `MAX_BINDINGS`，超限回收最久未用的
- `query(session)` 按会话查绑定，`export()` 导出绑定与换绑历史供值班回看
- `set_rollout([...])` 灰度：先小范围会话验证登录态，确认不丢再 `set_rollout(None)` 全量
