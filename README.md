# siteleasectl

现状：所有站点共用一个并发上限，租约无过期和优先级。

问题：大站吃光额度，慢站长期占用，进程退出后泄漏。

目标：按站点并发，优先级抢占，超时回收和检查点。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
