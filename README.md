# lineageimpact2

现状：只记录表级直接下游，字段参数被忽略。

问题：字段改动查不到影响，多跳没有层级，环路死循环。

目标：字段级多跳带路径，环路截断，结果可缓存和导出。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
