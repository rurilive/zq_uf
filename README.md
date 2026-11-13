# sessionkeep

现状：首次取数时登录一次，之后复用 cookie 访问页面。

问题：过期后仍复用旧 cookie，多线程同时刷新互相踢下线。

目标：提前刷新、单线程刷新加锁、登录页识别与请求重放。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
