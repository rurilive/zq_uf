# pagestore

现状：按页面ID整页写盘，重复内容各存一份。

问题：磁盘被重复内容占满，删除不回收块，并发写会读到半截文件。

目标：内容寻址加引用计数，原子写，块归零后回收。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
