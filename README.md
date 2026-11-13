# csvexport

纯标准库 CSV 导出：export(rows, path, options)，rows 可以是生成器，边读边写。

编码：默认 UTF-8，首行含非 ASCII 时自动写 BOM（bom: True/False 可强制）；encoding 可换 gbk 等。
转义：字段含分隔符、引号、换行时按 RFC 4180 加引号并双写引号，跨行字段不错位。
文本化：≥12 位纯数字和零开头编号写成 ="123" 形式，Excel 不转科学计数、不丢前导零。
方言：delimiter / quotechar / lineterminator 可配，默认逗号、双引号、CRLF。
校验：columns 固定列顺序并写表头，required 列不能为空，报错带行号和列名（ExportError）。
分片：shard_size 按数据行切分 name.0001.csv，name.manifest.json 记录每片行数和 CRC32。
续传：分片导出默认校验已有分片校验和并跳过已导行数继续，resume: False 可关闭。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
