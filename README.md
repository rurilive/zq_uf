# contenttype

现状：只按 URL 后缀判断类型，统一按文本解析。

问题：错误页被当正常页，JSON 与 XML 混用，二进制崩进程。

目标：头、魔数和结构三层探测，错误页指纹和二进制处理。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
