# fingerprint

现状：按 URL 字符串判重，进程内集合。

问题：参数顺序、大小写、追踪参数都算新链接，重启就丢。

目标：URL 与正文两级指纹、近重复阈值、持久化和窗口淘汰。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
