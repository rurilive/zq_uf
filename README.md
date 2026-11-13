# tlssafe

采集器的证书异常处理模块，纯 Python 标准库，入口为 `fetch(url)`。
证书错误（`ssl.SSLCertVerificationError`，含 URLError 包装）不重试，
抛出 `CertError`（`FetchError` 子类），携带站点、证书主题和到期时间；
网络等其他错误按 `retries` 重试后抛 `FetchError`。

```bash
python3 demo.py                      # 本地证书模拟三类错误 + 放行
python3 -m unittest discover -s tests
```

## 错误分类（CertError.kind）

- `expired`：证书过期（verify_code 10 或消息含 expired）
- `hostname_mismatch`：主机名不匹配（verify_code 62 或消息含 hostname / doesn't match）
- `incomplete_chain`：链不完整（verify_code 20/21 或 unable to get local issuer）
- `unknown_cert`：其他证书校验失败，同样不重试、明确报错
- `CertError.action` 为处理结果：`rejected` / `bypassed`（放行成功不抛错）/ `bypass_failed`

## 临时放行流程

1. `allow_host(host, minutes=60, reason="...")` 把站点加入放行名单，到期自动失效。
2. 名单内站点命中证书错误时，用免校验 opener 重发请求，并写一条审计
   （JSONL，字段含 url/host/kind/证书主题/到期时间/放行原因/result）。
3. `set_audit_log(path)` 指定审计文件；`revoke_host(host)` 提前移除；
   放行失败会抛 `CertError(action="bypass_failed")` 并写审计，不静默跳过。

## 参数和阈值

- `fetch(url, retries=1)`：仅对非证书错误重试，默认 1 次。
- `ALLOWLIST_DEFAULT_MINUTES = 60`：放行默认有效期（分钟）。
- `CERT_INFO_TIMEOUT = 5`：抓取对端证书详情（主题/到期时间）的连接超时（秒），
  抓不到记为 `unknown` 并打 warning，不静默。
- `set_opener(func)` 注入校验 opener；`set_unverified_opener(func)` 注入放行 opener；
  默认 opener 基于 urllib，`set_ca_file(path)` 指定 CA bundle。
