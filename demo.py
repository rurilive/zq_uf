import os
import tempfile

from textnorm import normalize

directory = tempfile.mkdtemp()

samples = {
    # Windows 导出：UTF-8 BOM + CRLF
    "win.csv": b"\xef\xbb\xbfid,name\r\n1,keyboard\r\n",
    # GBK 编码，未声明
    "gbk.csv": "编号,名称\r\n1,键盘\r\n".encode("gbk"),
    # 混合换行：CR / CRLF / LF
    "mixed.csv": b"a\rb\r\nc\n",
}

for name, data in samples.items():
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(data)
    result = normalize(path)
    print(f"{name}: encoding={result['encoding']} had_bom={result['had_bom']} "
          f"lines={result['lines']} line_map={result['line_map']} "
          f"bad_lines={result['bad_lines']}")
    print(f"  text: {result['text']!r}")
    print(f"  out:  {result['out']}")
