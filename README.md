# crawlledger

现状：采集条目直接追加，重复写也照记。

问题：重放重复记账，写一半崩溃损坏，并发写互相覆盖。

目标：按批次和序号幂等写入，临时文件原子改名，坏块可校验修复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
