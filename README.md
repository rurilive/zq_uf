# robotscache

现状：不拉取 robots，所有链接放行。

问题：违规抓取，规则更新不失效，子域共用一份。

目标：按主机缓存规则，过期后台刷新，失败保留旧规则。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
