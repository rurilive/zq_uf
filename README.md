# rowversion

现状：版本只是入参，写入直接覆盖。

问题：并发覆盖，冲突无提示，重试无上限，删除可被旧值复活。

目标：版本校验，冲突可合并，退避重试和待人工队列。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
