# langdetect2

现状：只看响应头，缺头时按英文处理。

问题：多语言页面只按整页判断，主语言经常判错，低置信页面也直接入库。

目标：按段落识别语言和占比，低置信标记并保留样本。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
