# tiering

现状：遍历目录把全部文件标成热层，apply 只回显数量不搬数据。

问题：冷数据占着高成本存储，热查询被拖慢，迁移靠人工容易漏。

目标：按访问频率和最近访问分热温冷，生成可干跑的迁移计划并校验。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
