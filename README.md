# idemwrite

现状：按主键覆盖，重跑结果一致。

问题：版本回退、并发覆盖和分批部分成功。

目标：版本比较、条件更新、分批重试和冲突重读。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
