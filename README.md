# metastore

现状：登记字段名，比较增删。

问题：类型和注释过期，名称不统一，下游不知道变更。

目标：字段推断、差异分类、别名归一和版本历史。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
