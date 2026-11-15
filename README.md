# loginkeep

现状：每条请求重新登录，令牌不共享也不续期。

问题：账号被风控，过期无预判，多进程互相踢下线。

目标：登录态池加提前续期，同账号共享令牌，失败冷却。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
