# textnorm

数据文件编码和换行规范化工具，纯 Python 标准库。入口 `normalize(path, encoding="utf-8")`，
结果写入 `path + ".norm"`，流式处理，内存占用与文件大小无关。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 编码口径

- 输出统一为 UTF-8 无 BOM；输入的 BOM（UTF-8/16/32）会被识别并剥离。
- 无 BOM 时按 `encoding` 参数 -> gb18030（GBK 超集）-> latin-1 顺序嗅探，实际编码见返回的 `encoding`。
- 无法解码的字节不丢：原行保留，行内以 U+FFFD 标记，行号记入 `bad_lines`。
- 大文件传 `keep_text=False`，不累积返回文本，内存恒定。

## 行号口径

- CRLF、CR、LF 统一为 LF，换行归一化是一对一的，行数不变。
- `line_map[i]` 是输出第 i+1 行对应的原始文件 1 起始行号；`bad_lines` 也用原始行号，报错可直接回到原文件。
- `lines` 为输出中 LF 的数量；末尾无换行的最后一行仍计入 `line_map`。
