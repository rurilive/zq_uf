import json

from ssoflow import login, report, reset

reset()

# 1) 多因素失败：归类到 mfa 环节，原因 mfa_failed，并给出后续动作。
login({
    "user": "bob",
    "events": [
        {"app": "mail", "step": "password", "ok": True, "ts": 1000},
        {"app": "mail", "step": "mfa", "ok": False, "ts": 1020},
    ],
})

# 2) 跨应用链路：mail -> vpn -> wiki，保留顺序与每次跳转耗时。
login({
    "user": "carol",
    "events": [
        {"app": "mail", "step": "password", "ok": True, "ts": 2000},
        {"app": "vpn", "step": "token", "ok": True, "ts": 2030},
        {"app": "wiki", "step": "session", "ok": True, "ts": 2100},
    ],
})

# 3) 同一账号连续失败，记录与上一次失败的间隔。
login({"user": "bob", "events": [
    {"app": "vpn", "step": "mfa", "ok": False, "ts": 1100}]})

# 4) 多因素被绕过：要求 MFA 却没有 MFA 环节，单独归为异常。
login({
    "user": "gina",
    "events": [
        {"app": "mail", "step": "password", "ok": True, "mfa_required": True, "ts": 3000},
        {"app": "mail", "step": "session", "ok": True, "ts": 3010},
    ],
})

print("== bob 最近一次登录的解释 ==")
print(json.dumps(login({"user": "bob", "events": [
    {"app": "mail", "step": "password", "ok": False, "reason": "account locked", "ts": 1200},
]}), ensure_ascii=False, indent=2))

print("\n== 全局 report ==")
print(json.dumps(report(), ensure_ascii=False, indent=2))

print("\n== 运维按账号 bob 查看异常 ==")
print(json.dumps(report(user="bob")["abnormal"], ensure_ascii=False, indent=2))
