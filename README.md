# textnorm

现状：按调用方给的编码整文件解码，只把CRLF换成LF。

问题：BOM留着，GBK当UTF8整行乱码，单独CR和混合换行让行号错位。

目标：识别BOM和真实编码统一转UTF8，流式处理并保留原文件行号映射。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
