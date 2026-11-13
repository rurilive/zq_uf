# signparam

配置驱动的接口签名参数生成器，纯 Python 标准库。接口为
`sign(params, rule=None)`，返回：

- `sign`：签名十六进制串
- `raw`：参与签名的拼接串（调试比对用）
- `snapshot`：参与签名的参数快照（含自动生成的时间戳/随机串），
  用同一规则对 snapshot 再次签名即可回放验证历史签名

## 规则字段

| 字段 | 取值 | 默认 | 说明 |
| --- | --- | --- | --- |
| `sort` | `asc` / `desc` / `none` | `asc` | 参数键排序，`none` 保持传入顺序 |
| `encoding` | Python 编码名 | `utf-8` | 拼接串与密钥的编码 |
| `quote` | bool | `False` | 是否对键和值做 URL 百分号编码 |
| `kv_sep` / `pair_sep` | 非空字符串 | `=` / `&` | 键值、参数对连接符 |
| `algo` | `md5` / `sha1` / `sha256` / `hmac-md5` / `hmac-sha1` / `hmac-sha256` | `md5` | 简单哈希或 HMAC |
| `secret` | 字符串 | `""` | HMAC 必填非空；非 HMAC 必须留空 |
| `timestamp` | `{"param": "ts", "format": ...}` | `None` | 时间戳参数名与格式 |
| `nonce` | `{"param": "nonce", "length": 16}` | `None` | 随机串参数名与长度 |

## 参数与阈值

- `params` 必须是非空 dict；键为非空字符串；值为非空字符串或数字
  （数字按 `str()` 转换；布尔、列表等类型直接报错）。
- 时间戳格式：`epoch` 为 10 位数字秒，`epoch_ms` 为 13 位数字毫秒，
  `iso8601` 为 `YYYY-MM-DDTHH:MM:SSZ`（UTC）。
- 随机串为 1–64 位字母数字（`A-Za-z0-9`）。
- 参数中已带时间戳/随机串时按上述格式校验，不符则抛 `SignError`；
  未带时自动生成并写入 `snapshot`。所有非法输入都抛出
  `SignError`（`ValueError` 子类），不会静默跳过。

## 调试方法

签名对不上时，比较两端的 `raw` 拼接串即可定位排序、编码或分隔符
差异；规则改动后用 `snapshot` 回放：

```python
from signparam import sign
old = sign(params, old_rule)
assert sign(old["snapshot"], old_rule)["sign"] == old["sign"]  # 回放验证
```

## 运行

```bash
python3 demo.py                       # 固定参数演示，输出与 tests 断言一致
python3 -m unittest discover -s tests # 37 条用例
```
