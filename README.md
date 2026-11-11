# guardwire

现状：按原始 URL 和请求头做关键字匹配。

问题：编码变体、重复参数、请求体和分块传输没有统一处理。

目标：补完整请求归一化、重复参数、请求体递归检查、分块拼接和规则索引。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
