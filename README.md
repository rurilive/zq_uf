# crawlquota

现状：线程、文件句柄和内存估算各有一个全局上限，超额直接抛错。

问题：任务之间互相抢，运行中超额不拦，慢任务拖垮整机。

目标：运行时配额，超额排队等待，按任务记账并输出等待时长。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
