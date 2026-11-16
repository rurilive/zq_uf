"""演示：覆盖全部入口的核查、弱项分级、到期提醒、配置对比与整改后重查。"""

from tlsc import (
    check,
    compare,
    expiring,
    recheck,
    report,
    reset,
    update,
)

reset()

SEVERITY = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}
CATEGORY = {"protocol": "协议", "cipher": "套件", "certificate": "证书"}
VERDICT = {"fail": "不合格", "warn": "待整改", "ok": "合格"}


def show_endpoint(endpoint):
    cert = endpoint["certificate"]
    redirect = "已配置" if endpoint["redirect_ok"] else "缺失"
    print(f"  {endpoint['endpoint']} ({endpoint['address']}:{endpoint['port']}) "
          f"-> {VERDICT[endpoint['verdict']]}")
    print(f"    协议 {', '.join(endpoint['protocols'])}；"
          f"套件 {', '.join(endpoint['ciphers'])}")
    print(f"    证书剩余 {cert['days_left']} 天（{cert['status']}）；"
          f"签发链{'完整' if cert['chain_complete'] else '不完整'}；重定向 {redirect}")


def show_weak(item):
    print(f"    [{SEVERITY[item['severity']]}] {CATEGORY[item['category']]} "
          f"实际={item['actual']} 期望={item['expected']}（{item['detail']}）")


print("== 1. 只看主入口会漏：shop 三个入口逐入口结论 ==")
shop = check("shop.example.com")
for endpoint in shop["endpoints"]:
    show_endpoint(endpoint)
print(f"  域名结论: {VERDICT[shop['verdict']]}，弱项 {shop['weak_count']} 项")

print("\n== 2. 弱项按入口排列，每项给出实际值和期望值 ==")
current = None
for item in shop["weak"]:
    if item["endpoint"] != current:
        current = item["endpoint"]
        print(f"  {current}:")
    show_weak(item)

print("\n== 3. 到期提醒：提前给出天数，便于安排更换 ==")
for reminder in expiring(30)["endpoints"]:
    print(f"  {reminder['host']}/{reminder['endpoint']} 剩 {reminder['days_left']} 天"
          f"（{reminder['status']}）")

print("\n== 4. 同一域名多个入口的配置差异 ==")
diff = compare("shop.example.com")
for field in diff["divergent"]:
    row = diff["fields"][field]
    groups = "; ".join(f"{value} -> {', '.join(names)}"
                       for value, names in ((g["value"], g["endpoints"])
                                            for g in row["groups"]))
    print(f"  {row['label']} 不一致: {groups}")
print(f"  偏离多数且更弱的入口: {diff['deviations']}")
print(f"  最该先整改: {diff['weakest_endpoint']}")

print("\n== 5. 整改后只重查变化的入口 ==")
print(f"  整改前 recheck: 重查 {recheck()['changed']} 个入口")
update("shop.example.com", "旧支付回调",
       protocols=["TLSv1.2", "TLSv1.3"],
       ciphers=["ECDHE-RSA-AES128-GCM-SHA256"],
       redirect="https://shop.example.com/",
       chain_complete=True,
       chain=["shop.example.com", "Example Intermediate CA", "Example Root CA"],
       days_left=365)
result = recheck("shop.example.com")
print(f"  整改旧支付回调后: 重查 {result['changed']} 个，跳过 {result['unchanged']} 个")
print(f"  本次重查: {result['checked']}")
fixed = next(e for e in result["hosts"][0]["endpoints"] if e["endpoint"] == "旧支付回调")
show_endpoint(fixed)

print("\n== 6. 全量汇总 ==")
out = report()
print(f"  域名 {len(out['hosts'])} 个，入口 {out['endpoints']} 个，弱项 {out['weak_count']} 项")
print(f"  按分类: { {CATEGORY[k]: len(v) for k, v in out['weak_by_category'].items()} }")
print(f"  按严重程度: { {SEVERITY[k]: len(v) for k, v in out['weak_by_severity'].items()} }")
print(f"  结论: 合格 {out['verdict']['ok']}，待整改 {out['verdict']['warn']}，"
      f"不合格 {out['verdict']['fail']}")

print("\n== 7. 未知域名与不合法配置都会明确报错 ==")
try:
    check("mail.example.com")
except ValueError as exc:
    print(f"  {type(exc).__name__}: {exc}")
try:
    update("shop.example.com", "主入口", protocols=["TLSv2.5"])
except ValueError as exc:
    print(f"  {type(exc).__name__}: {exc}")
