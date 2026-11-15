# loginkeep

现状：每条请求重新登录，令牌不共享也不续期。

问题：账号被风控，过期无预判，多进程互相踢下线。

目标：登录态池加提前续期，同账号共享令牌，失败冷却。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

```python
from loginkeep import LoginPool, lease, refresh
a = lease("acc-1")            # 同账号共享同一份登录态，多机器部署时池子放登录态服务后面
r = refresh(a["token"])       # 临近过期自动续期；被踢则单飞重登一次
# 返回：expires_in 剩余秒数、refreshed 是否续期过、relogin 是否重登过、
# paused_until 暂停到何时、error.kind 区分 account（账号）/site（站点）
# 失败按账号暂停，到期自动小流量试登录，连续成功自动恢复，无需人工重启
pool = LoginPool(authenticator, pause_seconds=60)
pool.start_background_refresh()  # 后台提前续期
```
