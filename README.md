# crawlrecover

现状：已完成页面只记在内存，恢复时从零重跑。

问题：位点丢失，日志半行恢复失败，重复写已完成页面。

目标：位点与校验和落盘，恢复跳过已完成页，重复恢复幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
