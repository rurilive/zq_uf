# trafficmask

现状：请求按行原样落盘，cookie和手机号明文，回放原样读出。

问题：共享环境能看到凭据；回放头对不上签名失效；跨天文件串不起来。

目标：可脱敏录制与确定性回放，带批次号、跨文件检索和演练隔离。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
