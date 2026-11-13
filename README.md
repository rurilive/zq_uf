# filefetch

现状：一次性下载附件，整体写入目标文件。

问题：中断只能重下，同名覆盖，没有校验。

目标：范围续传、重名规避、完整性校验和并发限流。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
