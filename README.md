# crawldiagnose

现状：断点只记方法、URL、状态和响应摘要，回放就是读回这条记录。

问题：头和体丢了复现不了，运行时cookie和签名无法重建，没有差异对比。

目标：确定性重放加结构化对比，分请求变化、响应变化和状态变化三类。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
