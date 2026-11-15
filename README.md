# crawlguard

现状：所有采集任务直接入队，名单只在启动时读。

问题：重复和越权任务混进来，名单变更要重启，准入没有审计。

目标：按来源、站点和名单校验，名单原子热更新，拒绝写审计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
