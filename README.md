# flowstep

现状：按顺序执行步骤，上下文用字典传递。

问题：一步失败整条重跑，非幂等步骤重复执行。

目标：断点续跑、幂等跳过、业务错误码和条件分支。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
