# guardwire

网关请求检查库，纯 Python 标准库。入口仍是 `inspect_request(request, rules)`，
返回 `allowed`、`rule_id`、`evidence`（含归一化证据与原始片段）、`normalized`
和 `match`。

归一化口径：百分号 / `%u` / `\u` / `\x` 转义反复解码，路径点段、重复斜杠和
反斜杠折叠，方法、路径、请求头与参数名值、请求体值统一小写。查询参数保留全部
同名值并逐个检查；请求体先拼接 chunk（分片列表或 HTTP chunked 帧）再解析，支持
JSON、表单和纯文本；JSON 递归检查嵌套对象与数组并保留重复键，声明为 JSON 但
解析失败按拒绝处理（`rule_id = body-parse-error`）。

规则 `Rule` 可组合 `path_prefix`、`method`、`headers`、`query`、`body_field` /
`body_fields`、`needle`，所有给出的条件都命中才算命中。`evidence.match` 同时给出
`normalized_fragment` 和 `original_fragment`；规则按方法和路径前缀建索引，并按
声明顺序返回第一条命中的规则。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
