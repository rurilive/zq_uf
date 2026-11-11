# guardwire

纯 Python 标准库的网关请求检查库，入口保持为 `inspect_request(request, rules)`。

- 统一归一化：多层百分号编码、`\uXXXX` Unicode 转义、路径点段（`.`/`..`）、重复斜杠和大小写都收敛为同一表示后再匹配。
- 查询/表单重复参数保留全部值逐个检查；JSON 请求体递归展开嵌套对象与数组，解析失败直接拒绝（`rule_id=malformed-body`）。
- `Transfer-Encoding: chunked` 的请求先按 RFC 7230 拼完整再检查，分块帧错误同样拒绝；纯文本体按关键字检查。
- `Rule` 在原有字段上新增 `header`、`query_param`、`body_field`，与 `path_prefix`/`method`/`needle` 以 AND 组合，header/param/field 支持 `名:值`、`名=值` 子串条件，留空只按名称匹配。
- 规则按方法分桶、按归一化路径前缀建 trie 索引；命中返回规则 id、`normalized` 归一化证据和 `matches` 原始片段。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
