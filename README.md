# tableversion

现状：提交直接覆盖表内容，不保留历史。

问题：回滚没有目标，版本和结构变更不关联，并发提交互相覆盖。

目标：每次提交生成版本和校验和，回滚校验版本链，并发提交串行。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
