# signparam

现状：键名排序后直接拼接取md5，时间戳和随机串由调用方传入。

问题：算法和拼接写死，加字段要发版；编码和排序不一致时同一请求签名不同。

目标：配置驱动的签名，支持排序、编码、时间戳和HMAC，规则可回放。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
