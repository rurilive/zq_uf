# crawlpause

现状：暂停只置内存标志，在跑任务不等待。

问题：硬杀丢结果，恢复从头抓，多任务无法分别控制。

目标：按任务暂停，等收尾，检查点恢复和幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
