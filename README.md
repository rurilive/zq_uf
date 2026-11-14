# dedupwindow

现状：去重键永久保留，水位不清理状态。

问题：跑久了内存爆，迟到数据判成新数据，重启加载慢。

目标：按水位有界去重，窗口外清理，迟到可判，状态可序列化。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
