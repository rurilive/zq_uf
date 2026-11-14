# retrywindow

现状：失败后立即重试，最多三次，各任务独立。

问题：站点压力叠加，没有时间上限，不区分失败和放弃。

目标：按站点共享窗口，抖动，放弃标记和取消。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
