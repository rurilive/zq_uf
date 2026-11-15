# loginkeep

现状：每条请求重新登录，令牌不共享也不续期。

问题：账号被风控，过期无预判，多进程互相踢下线。

目标：登录态池加提前续期，同账号共享令牌，失败冷却。

实现：`loginkeep.configure(site, store=FileStore(共享目录))` 后，
`lease(account)` 返回共享令牌与 `expires_in / refreshed / relogin / paused_until / rate`，
`refresh(token)` 过期前续期、被踢只重登一次；登录失败按 `error.kind`（`account`/`site`）
按账号暂停并指数退避，暂停结束自动试探登录，连续成功后恢复并发。
`site` 需实现 `login(account)` / `refresh(token)`，返回 `LoginResult(token, ttl)`；
可参考 `loginkeep.simulator`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
