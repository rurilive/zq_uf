# sessionkeep

采集登录页的登录态保持小模块，纯 Python 标准库。对外接口保持不变：
`ensure_session()` 取可用 cookie，`fetch(url)` 带登录态取数。

## 刷新口径

- 过期时间统一按 UTC 计算（带时区的 `datetime`，或 epoch 秒；无时区的
  `datetime` 视为 UTC），机器时区变化不影响有效性判断。
- `login()` 返回 `cookie` 字符串，或 `(cookie, expires_at)` 二元组；也可以
  用 `ttl` 秒/timedelta 让模块自己算过期时间。
- 距过期不足 `refresh_margin`（默认 300 秒，可配置秒数/timedelta）即视为
  过期并提前刷新；无过期信息的 cookie 只在命中登录页时强制重登。
- 刷新在条件变量上单飞：同一时刻只有一个线程执行 `login()`，其余线程
  等待并复用同一个新 cookie，避免互相踢下线。
- `login()` 连续失败达到 `max_refresh_failures`（默认 3）次时抛
  `SessionRefreshError`，成功一次即清零计数。

## 重放口径

- `fetch(url)` 识别响应中的登录页特征（默认匹配 `type="password"` 等，
  可用 `login_markers` 自定义），空白/`None` 响应视为空页面。
- 命中登录页或空响应时，强制重登后把当前 `url` 原封不动重放一次，
  重放最多一次；仍异常分别抛 `LoginPageDetected` / `EmptyResponse`，
  不会把登录页或空页面当成功数据返回。

## 运行

```bash
python3 demo.py          # 模拟两小时时间推进
python3 repro.py         # 修复前问题复现，修复后退出码为 0
python3 -m unittest discover -s tests
```
