# crawlhold

现状：挂起信号到达时直接退出，在跑请求被硬断。

问题：半写文件遗留，代理和配额不释放，恢复不知道从哪继续。

目标：可恢复挂起，等在跑请求收尾并落检查点。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
