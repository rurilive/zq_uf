# formstate

现状：每步直接推进，重复提交会再发一次。

问题：重复提交生成两条记录，失败只能从头来，验证码步骤卡死。

目标：可重入状态机，幂等键，失败回退，验证码挂起和断点恢复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
