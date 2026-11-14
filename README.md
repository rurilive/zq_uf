# auditexport

现状：全量读出后一次写文件。

问题：内存爆，导出中断重来，分片无校验。

目标：分片导出，断点续导，缺片校验和幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
