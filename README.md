# fieldlineage

现状：按任务记录输入表和输出表，字段映射直接丢掉，查询只到表级。

问题：改字段要人工翻SQL，字段改名后血缘断掉，多跳关系看不到。

目标：字段级血缘，解析别名和聚合，保留改名历史，支持多跳查询。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
