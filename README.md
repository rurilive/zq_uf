# ruletemplate

现状：规则由调用方整份传入，没有模板、继承和覆盖。

问题：每个数据集复制一套，字段改名要全改，冲突时不知道以谁为准。

目标：公共模板加数据集覆盖，冲突报错，模板版本化并支持迁移回测。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
