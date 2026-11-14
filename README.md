# uafilter

现状：所有站点共用一套请求头。

问题：站点拒绝，UA 被封无备用，语言编码不匹配。

目标：按站点头池，封禁切换，冷却和编码匹配。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
