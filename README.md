# metastore

元数据登记中心：从数据推断字段，差异分类，别名归一，版本历史。

字段口径：`name` 为别名归一后的规范名；`type` ∈ int/float/bool/str/unknown，由非空值推断（int+float 升为 float）；`null_rate` 为空值占比（空串/null/none/na 计空），保留 4 位小数。
版本口径：每次 `register` 生成从 1 起递增的 `version`；`history(name)` 查全部版本，`get(name, version)` 查指定快照。
diff 口径：分 `added`/`removed`/`type_changed` 三类；有差异时 `affected_tasks` 列出下游任务，无差异为空。
别名冲突：多个字段归一到同一规范名时，保留来源中本名即规范名的字段。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
