# taskhandoff

现状：交接只改任务指向，停旧和起新之间没有检查点。

问题：迁移有空窗，旧任务结果未确认，失败两边都不完整。

目标：双写幂等键，追上后停旧，失败可回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
