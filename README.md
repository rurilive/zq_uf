# schemagray

现状：结构变更一次应用到全部表。

问题：出问题影响所有下游，灰度不看读写方，回滚靠手工。

目标：按表、任务和读写方向分批，观察错误率，异常自动回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
