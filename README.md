# partitiongc

现状：按目录修改时间判断过期，逐目录删除。

问题：命名不规整会漏，删前不查下游，一次删太多会超时。

目标：按数据时间识别、租约保护、分批提交和审计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
