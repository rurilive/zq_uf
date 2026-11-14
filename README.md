# wmsync

现状：全局水位取各源最大值。

问题：快源甩开慢源，空闲源不推进，迟到数据没有标记。

目标：取最慢源，空闲心跳，迟到侧输出，状态可恢复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
