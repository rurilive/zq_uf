# archivepack

现状：附件按URL末段命名写盘，重名覆盖，查询参数进文件名，没有清单。

问题：大附件全读进内存，恢复时不知道缺哪些，跨批次无法去重。

目标：内容哈希命名、流式续传、引用计数去重和清单校验恢复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
