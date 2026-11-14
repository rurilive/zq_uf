# filededup

现状：按文件名复制，同内容不同路径各存一份。

问题：仓库翻倍，删一份影响共享，回收靠人肉。

目标：内容寻址加引用计数，归零才删，并发安全。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
