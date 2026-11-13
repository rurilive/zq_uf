# fileindex

现状：遍历目录记录修改时间，比较时只看时间戳。

问题：拷贝后时间变了就重跑，内容变化但时间没变会漏，删除不清理。

目标：按大小和分块哈希的增量索引，未变跳过，删除同步清理。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
