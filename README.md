# accountrefresh

按账号单飞的登录态刷新：同一账号同时只有一个刷新在跑，其余等待复用结果，账号之间互不阻塞。

- 提前量：距过期不足 30s（`EARLY_REFRESH_S`）即提前刷新；无过期信息的令牌视为立即过期，保持每次请求都刷新。
- 退避：失败按 1s 起步指数翻倍、60s 封顶（`BACKOFF_BASE_S`/`BACKOFF_MAX_S`），`next_try` 前不再尝试。
- 隔离：连续失败 3 次（`ISOLATE_AFTER`）隔离 300s（`ISOLATION_S`），调度权重按 2^-失败次数 降级，隔离期归零。
- 审计：每次成功/失败/隔离写审计（`audit_log()`，`set_audit_file(path)` 可落盘 JSON Lines）。

接口：`get_token(account)`、`refresh(account, force=False)`、`stats()`、`report()`（刷新次数、等待复用数、退避剩余秒、隔离账号、调度权重）。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
