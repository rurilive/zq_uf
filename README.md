# crawlmanifest

现状：一个任务一份清单，直接覆盖。

问题：清单和产物不同步，写一半损坏，并发覆盖。

目标：分片清单，原子写，校验修复和差异恢复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
