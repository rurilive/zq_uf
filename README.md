# formstep

现状：每步重新取令牌，步骤状态不保存。

问题：并发共用令牌被判重放，回退后流程重来。

目标：按流程保存令牌和步骤，串行推进，回退重取。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
