# accountrefresh

现状：每次取令牌都刷新，没有过期判断和退避。

问题：并发刷新让旧令牌立刻失效，失败把账号锁死，账号之间互相拖累。

目标：按账号单飞，提前刷新，指数退避和账号隔离。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
