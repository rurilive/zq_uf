# idemwrite

现状：按主键直接覆盖，没有版本和幂等键。

问题：旧值冲掉新值，重复提交重复写，批量半成功。

目标：版本保护，幂等重放，批量原子和冲突记录。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
