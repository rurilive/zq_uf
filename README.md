# exploitchain
安全方向的小工具，用来扫描上传目录里的可疑脚本。

## 判定方式
不再按关键字裸匹配，而是内容 + 行为结合，按权重打分（阈值见 `webshell/scan.py`）：

- **内容规则**：匹配真实调用语法（`eval(`、`$f(`、`$_POST['x'](`、`preg_replace /e`、动态 include 等），
  先剥注释再匹配，`evaluation`、注释里的 `eval`、数组键 `system` 这类不再误报；
- **静态还原**（不执行代码）：拼接折叠 `'ev'.'al'`、`chr()` 链、`\x65` 转义、
  `base64_decode/gzinflate/str_rot13/strrev/urldecode/hex2bin` 解码，支持编码串先赋值变量再间接传入；
- **行为信号**：文件创建时间（24h 内新建）、访问日志（可疑参数、异常状态码）只加权，不单独定罪；
- **分组**：同目录文件做规范化（注释/字符串/变量名/数字归一化），相似度 ≥0.8 或还原载荷相同即合并成组；
- **白名单**：按精确文件路径跳过，不影响同目录其他文件。

## 调用方式
`scan(path)` 和 `report()` 的签名不变。`path` 仍是文件名到内容的映射，value 传字符串即兼容旧用法；
需要带行为信息时 value 传 dict：

```python
scan({
    "upload/shell.php": {
        "content": "<?php system($_POST['cmd']);",
        "created_at": "2026-11-17T02:00:00Z",
        "access": [{"method": "POST", "post": "cmd=whoami", "status": 200}],
    }
})
```

## 返回内容
- `scan()`：`scanned/hits/files` 保持原字段，新增 `findings`（每条含文件、分数、风险等级、
  合并后的命中次数、行号、命中行+前后各一行上下文、还原记录、行为信号、组 ID）和 `groups`；
- `report()`：保留 `total/files/counts/whitelist`，新增 `findings`、`groups` 和按目录汇总的 `by_directory`；
- 同一文件多次命中合并为一条 finding，`count` 记录次数；
- `whitelist(path)` 精确加白，`reset()` 清空上一轮结果和白名单。

## 运行
```
python3 demo.py
python3 -m unittest discover -s tests
```
