# robotscache

现状：每次判断都重新拉取robots，没有缓存文件，也没有过期时间。

问题：站点被重复请求压垮，规则更新后仍按旧值走，多进程各拉一份。

目标：按host的可过期共享缓存，失败沿用旧规则并记录告警。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
