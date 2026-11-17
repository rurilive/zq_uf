"""演示：按域名核查全部入口、汇总报告、入口对比与整改后增量重查。"""

import json

from tlsc import check, diff_entries, expiring, mutate_entry, recheck, report


def show(title, payload):
    print(f"===== {title} =====")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print()


# 第一段：单域名核查（每个入口单独给结论）与全量汇总
show("check('shop.example.com')：三个入口各自的证书/协议/套件/重定向结论",
     check("shop.example.com"))
show("expiring(30)：30 天内到期（含已过期）的入口，按紧急程度排序",
     expiring(30))
show("report()：全部域名的弱项分级与到期提醒", report())

# 第二段：同域名多入口对比 + 整改后只重查变化的入口
show("diff_entries('cdn.example.com')：只列取值不一致的配置字段",
     diff_entries("cdn.example.com"))

recheck("shop.example.com")  # 建立基线
mutate_entry(
    "shop.example.com",
    "primary",
    protocol="TLSv1.3",
    cipher="TLS_AES_256_GCM_SHA384",
)
show("整改 primary 后 recheck：仅 primary 重查，其余入口走缓存",
     recheck("shop.example.com"))
