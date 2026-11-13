# fieldsource

现状：按规则顺序取值，只留最终值，后命中的规则覆盖前面的。

问题：字段来源、原始片段和候选规则全丢，规则改名后历史数据对不上。

目标：字段级来源记录，保留全部候选、优先级、版本和跨页来源。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
