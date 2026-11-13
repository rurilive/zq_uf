# timeoutgov

现状：所有任务共用一个固定超时，超时直接抛错。

问题：大任务被误杀，小任务挂太久，超时没有现场，重试从头跑。

目标：按任务类型和历史耗时动态超时，保存进度和堆栈，从断点继续。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
