# retryquota

现状：每个任务独立计数，请求一律获批。

问题：所有任务一起重试打满站点，没有全局上限，恢复后同时回满。

目标：站点和全局两级配额，超额排队，释放幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
