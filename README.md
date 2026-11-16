# dataclass

证书和协议核查小工具，覆盖同一域名的**全部入口**，逐入口给出弱配置与到期结论。

每个入口独立登记地址、端口、证书剩余天数、签发链、开放的协议版本、加密套件和 HTTPS 重定向；
弱项按协议版本 / 套件强度 / 证书问题分类，每项给出实际取值、期望值和严重程度。

## 入口

```python
check(host, endpoint=None, within=30)  # 核查一个域名的全部入口（或单个入口）
report(within=30)                      # 汇总全部域名的入口、弱项、到期提醒和整改状态
expiring(within=30)                    # 只看到期提醒，按剩余天数升序
recheck(host=None, within=30)          # 整改后只重查配置发生变化的入口
compare(host)                          # 对比同域名多入口的配置差异
```

登记与整改：`register(host, endpoint, config)`、`update(host, endpoint, **fields)`、
`remove(host, endpoint)`、`reset()`。错误类型见 `tlsc.errors`，均继承 `ValueError`。

## check(host) 返回

- `endpoints`：入口清单，每项含 `address`、`port`、`protocols`、`ciphers`、`protocol`（最弱版本）、
  `strongest_protocol`、`cipher`（最弱套件）、`certificate`（`days_left` / `status` / `expire_soon` /
  `expired` / `chain` / `chain_complete` / `chain_issues`）、`redirect`、`redirect_ok`、`weak`、
  `worst`、`verdict`、`notes`。
- `weak`：域名下全部弱项，**按入口排列**，同一入口内按严重程度降序。
- `weak_by_endpoint`：按入口分组的弱项，运维逐入口整改。
- `expiring`：进入提醒窗口的入口及剩余天数。
- `verdict`：`fail`（存在高/严重弱项）、`warn`（仅中低弱项或缺重定向）、`ok`。

## 弱项分类与分级

| 分类 | 判定 | 严重程度 |
| --- | --- | --- |
| 协议版本 | `SSLv2` / `SSLv3` | critical |
| | `TLSv1.0` | high |
| | `TLSv1.1` | medium |
| 套件强度 | RC4 / NULL / EXPORT / ANON / MD5 | critical |
| | 3DES / DES（Sweet32） | high |
| | CBC 且无前向保密 | medium |
| | 静态 RSA 的 AEAD（GCM/CCM/CHACHA20） | low |
| 证书问题 | 已过期 | critical |
| | 剩余 ≤ 7 天、签发链不完整 | high |
| | 剩余 ≤ 30 天 | medium |

协议版本与套件判定基于名称，ECDHE / DHE / `TLS_*`（TLS 1.3）视为有前向保密，不报弱项。
重定向缺失不单列为弱项，记入 `notes` 并把入口结论降为 `warn`。

## 到期提醒

`within` 默认 30 天，可调。剩余天数 ≤ `within` 的入口进入 `expiring`，按天数升序，
可直接据此安排更换；同一域名的提醒按入口列出。

## 整改后只重查变化的入口

`check()` 会记录每个入口的配置指纹。`recheck(host)` 只对指纹变化的入口重跑判定，
未变化的直接复用上次结论，返回 `checked` / `skipped` / `changed` / `unchanged`。
提醒窗口（`within`）变化时对应入口也会重查，避免沿用旧窗口的结论。

## compare(host)

每个字段给出各入口取值、一致性与分组；`deviations` 只列出“偏离多数且更弱”的入口，
`weakest_endpoint` 指出最该先整改的入口。协议、套件列表比较时忽略顺序。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
