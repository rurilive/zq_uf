# contenttype

现状：只按 URL 后缀判断类型，统一按文本解析。

问题：错误页被当正常页，JSON 与 XML 混用，二进制崩进程。

目标：头、魔数和结构三层探测，错误页指纹和二进制处理。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

探测顺序（`dispatch(response)` 返回 `Detection`，含 type/confidence/handler/sample/reason）：

1. 错误页指纹：状态码 >= 400 且正文含错误关键词，或正文含强指纹（如 Traceback），置信度 0.9–0.95。
2. 响应头：Content-Type 明确指向 json/xml/html 时置信度 0.9；text/plain、octet-stream 等泛型头不作结论。
3. 魔数：前 512 字节匹配 PNG/JPEG/GIF/PDF/ZIP/GZIP 或含 NUL/高比例不可打印字节 → binary，0.95。
4. 结构：JSON 解析成功 → json（0.8）；`<?xml` 或可解析标签树 → xml；含 `<html`/doctype → html。
5. 兜底：URL 后缀（0.4），仍无信号默认 html（0.2）。

`parse(response)` 按类型分派处理器：json→对象，xml→Element，html/text/error→文本，
binary→附件 dict（校验 Content-Length，不符报 Failure）。解析失败返回 `Failure`（含 reason），不抛异常。
