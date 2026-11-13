# lineage

现状：记录每个任务的输入输出，查询直接消费者。

问题：SQL 临时表识别不到，改名断链，只看一跳。

目标：解析 SQL、消解临时表、别名和多跳展开。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
