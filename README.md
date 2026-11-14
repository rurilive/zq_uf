# retrybudget

现状：每层固定重试三次，预算字段没人读取。

问题：一次请求被放大几十倍，父子链路无法判断在哪层放弃。

目标：可传递预算，耗尽快速失败，兄弟调用不能透支。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
