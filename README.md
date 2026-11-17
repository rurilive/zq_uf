# tlsc

证书和协议核查小工具，按**域名 → 入口**两级给出弱配置与到期结论。

## 覆盖范围

同一域名的每个入口（主入口、备入口、灰度、CDN 边缘、HTTP 跳转入口）单独登记、单独给结论，
不再只查主入口。每个入口一起核查五项：

- **证书有效期**：剩余天数、是否即将到期（默认提前 30 天）、是否已过期
- **签发链**：自签名（链长 1）、链不完整（缺中间 CA，链长 2）
- **协议版本**：SSLv2/SSLv3 为 critical，TLSv1.0/TLSv1.1 为 high
- **加密套件**：RC4/EXPORT/NULL/ANON 为 critical，3DES 为 high，CBC-SHA1 为 medium
- **重定向**：HTTP 入口必须 301/302 跳到对应 HTTPS，否则为 medium 弱项

每条弱项含分类（`protocol` / `cipher` / `cert` / `redirect`）、严重程度
（`critical` > `high` > `medium` > `low`）、**实际取值**与**期望取值**。

## API

```python
from tlsc import check, report, expiring, diff_entries, recheck

check(host)            # 一个域名全部入口的逐项结论（证书状态、协议、套件、重定向、弱项）
report()               # 全部域名汇总：入口清单、按严重程度排序的弱项、到期提醒、分级计数
expiring(within=30)    # within 天内到期/已过期的入口，带剩余天数，按紧急程度排序
diff_entries(host)     # 同域名多入口配置对比，只返回取值不一致的字段
recheck(host=None)     # 增量重查：配置指纹未变的入口走缓存，只重查发生变化的入口
```

`recheck` 基于入口配置指纹（全部登记字段的哈希）工作：整改后未变化的入口直接复用上次结论，
变化的入口重新评估并在结论里标 `changed: true`，新增入口进 `new` 列表；`force=True` 可强制全量重查。
需要在多套环境间隔离缓存时，直接实例化 `tlsc.Checker`。

## 演示与测试

```bash
python3 demo.py
python3 -m unittest tests.test_probe -v
```
