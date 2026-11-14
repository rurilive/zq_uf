# pagestore

现状：按页面ID整页写盘，重复内容各存一份。

问题：磁盘被重复内容占满，删除不回收块，并发写会读到半截文件。

目标：内容寻址加引用计数，原子写，块归零后回收。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

块格式：正文按 UTF-8 编码后切成 4096 字节块，每块以 SHA-256 摘要命名存于
`blocks/<hex>`；清单 `manifests/<page_id>.json` 记录 page_id、encoding 和有序块
哈希列表；`blocks/refs.json` 记录每块引用计数。块与清单都先写临时文件再原子
改名（`os.link` 排他创建），并发写同一页面只有一个成功，其余返回冲突。

GC 口径：`delete` 只删清单并减引用，不碰块文件；`gc()` 回收引用计数归零且未被
任何清单引用的块，仍被引用的块跳过并列入 `skipped`。`put` 返回新增块、复用块
和冲突标记，`gc` 返回回收块与跳过块。引用计数按进程内锁串行更新，多进程并发
不在保证范围内。
