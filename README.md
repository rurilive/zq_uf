# loginkeep

账号级登录态管理：同账号共享一份登录态、到期前主动续期、被踢只重登一次、
失败按账号暂停并自动小流量恢复。解决“每条请求重新登录、多机互踢、
账号跑十几分钟被风控”的问题。

## 解决的问题

| 故障模式 | 机制 |
| --- | --- |
| 每条请求重新登录 | 账号级会话复用，多次 `lease` 返回同一令牌（`shared=True`），全集群只登录一次 |
| 多台机器各登各的、后登录踢掉前登录 | 跨进程单飞锁（single-flight）：同账号同时只允许一次真实登录，其余进程等待并复用结果 |
| 登录态没到期就被踢 | `renew` 报 `KickedError` 后作废旧令牌，单飞重登一次，返回里 `relogin=True` |
| 过期无预判 | 剩余有效期进入续期窗口（默认提前 5 分钟）后台静默续期，无需业务请求触发 |
| 失败原因不可见 | 异常分层：`AccountError`（账号/风控）与 `SiteError`（站点抖动），返回 `error_kind` + `pause_reason` |
| 失败后反复重试加剧风控 | 按账号指数退避暂停，暂停期间不发起任何新登录，返回 `paused_until`/`retry_after` |
| 恢复需要人工重启 | 暂停到期自动转 `TRIAL` 小流量试探，连续成功 N 次自动恢复满速，全程自动接单 |

## 快速开始

```bash
python3 demo.py
python3 -I -m unittest discover -s tests -t .
```

## 对外接口

```python
from loginkeep import lease, refresh

lease(account)   # 取账号当前共享登录态
refresh(token)   # 主动续期；被踢/过期则单飞重登
```

`lease` 返回字段：

| 字段 | 含义 |
| --- | --- |
| `token` / `expires_in` | 当前令牌与剩余可用秒数 |
| `shared` | 是否为账号级共享令牌（正常恒为 true） |
| `ever_refreshed` / `ever_relogin` | 该账号历史上是否发生过续期 / 重登 |
| `state` | `HEALTHY` 满速 / `TRIAL` 试探限流 / `PAUSED` 暂停 |
| `rate` | 速率档位：`full` / `trial` / `paused`，调度方据此限流 |
| `paused_until` / `retry_after` | 暂停到什么时间、还需等多少秒 |
| `error_kind` / `pause_reason` | `account` 或 `site`，及站点给出的具体原因 |
| `fail_count` | 连续失败计数（决定退避档位，恢复满速后归零） |

暂停时 `ok=false`、`token=null`，调用方应放下该账号转用其他账号，
到 `retry_after` 后再试——无需重启调度。

## 接入真实站点

实现 `LoginBackend` 两个方法，并把站点错误翻译成对应异常：

```python
from loginkeep import LoginBackend, LoginResult, AccountError, SiteError, KickedError, configure
from loginkeep.store import SqliteStore

class MyBackend(LoginBackend):
    def login(self, account):
        resp = site_login(account)              # 完整登录
        if resp.status == 403:
            raise AccountError(resp.msg)        # 风控/封禁：账号问题
        if resp.status >= 500:
            raise SiteError(resp.msg)           # 站点抖动
        return LoginResult(resp.token, resp.ttl, renewable=True)

    def renew(self, account, token):
        resp = site_renew(token)                # 静默续期，不触发单点互踢
        if resp.kicked:
            raise KickedError("kicked")         # 被踢：管理器单飞重登
        return LoginResult(resp.token, resp.ttl)

# 多机部署：所有机器挂同一个 SQLite 文件（NFS/共享盘），即拿到同一份登录态
configure(backend=MyBackend(), store=SqliteStore("/data/loginkeep/sessions.db"))
```

## 模块结构

```
loginkeep/
  errors.py    # AccountError / SiteError / KickedError 错误分层
  backend.py   # LoginBackend 抽象 + DummyBackend（演示/测试用假后端）
  store.py     # SessionRecord 状态；InMemoryStore 与 SqliteStore（含跨进程单飞锁）
  manager.py   # 状态机、提前续期、单飞重登、指数退避、TRIAL 自动恢复
  login.py     # 兼容垫片，lease/refresh 实现在 manager.py
```

生产环境若已有 Redis/MySQL，实现 `store.Store` 接口替换即可，管理器无需改动。
纯标准库实现，无第三方依赖。
