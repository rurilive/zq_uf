# sessionvault

会话加密落盘：信封 JSON（key_version + nonce + checksum + payload），
流式异或（HMAC-SHA256 计数器 keystream）加密，HMAC-SHA256 校验，纯标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 密钥口径

- 密钥存 `<root>/_keys.json`：`{"current": N, "keys": {"1": hex, ...}}`，首次使用随机生成，权限 0600。
- 每条记录信封带 `key_version`，解密按版本取钥，旧版本密钥保留以读旧记录。
- `rotate(root)`：先用新密钥把全部记录暂存重写、逐个 `os.replace`，最后才切换 `current` 指针。
- 任一步失败即回滚：清理暂存、恢复原始文件、指针留在旧版本，返回 `status="rolled_back"` 与 `error`。
- `current_key_version(root)` 返回当前版本；轮换遇到的损坏记录跳过并隔离，不阻断轮换。

## 隔离口径

- 解密失败（JSON 非法、缺字段、未知版本、checksum 不符）即判定损坏。
- 损坏文件移到 `<root>/quarantine/<account>-<ns>.json`，原文件删除。
- `quarantine/index.json` 追加记录：account、reason、file、quarantined_at。
- `load` 抛 `CorruptRecordError`（带 `reason` 与 `quarantine_path`），只影响该账号。
- `quarantined(root)` 返回全部隔离记录。
