# schemamigrate

现状：只支持向前改版本号，没有校验和回退。

问题：升级后回不去，新旧版本并行字段对不上，半途失败留下混合版本。

目标：双向迁移，逐步校验，失败保留现场，支持回退点。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
