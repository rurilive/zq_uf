# linkcheck

现状：串行检查，每个链接都请求一次。

问题：巡检慢，重定向链不完整，重复检查。

目标：并发去重，完整重定向链，环检测和分类统计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
