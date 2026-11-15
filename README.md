# scrollload

现状：固定滚动三次，短列表浪费时间，长列表抓不全。

问题：没有等待条件，到底判断靠高度。

目标：按新增条目和加载指示器条件滚动，虚拟列表去重。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
