# selectorwatch

现状：按站点统计非空字段总数，不区分具体字段，也没有基线。

问题：选择器失效要等入库空值才暴露，告警只有总数定位不到字段。

目标：按站点和字段的命中率与结构基线巡检，低于阈值或突变才告警。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
