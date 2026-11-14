# retentiontag

现状：标签只挂主表，直接覆盖。

问题：下游不继承，豁免永久，删除不看依赖。

目标：标签继承，收紧限制，豁免到期和分批删除。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
