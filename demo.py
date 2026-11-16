from ssoflow import login, report, reset

reset()

# 1. MFA 失败：原因、环节、后续动作都能解释
print(login({
    "user": "bob",
    "events": [
        {"app": "mail", "step": "password", "ok": True, "ts": 1000},
        {"app": "mail", "step": "mfa", "ok": False, "ts": 1005},
    ],
}))

# 2. 跨应用跳转：链路保留应用顺序与每跳耗时；MFA 被绕过单独记异常
print(login({
    "user": "carol",
    "events": [
        {"app": "mail", "step": "password", "ok": True, "ts": 1000},
        {"app": "vpn", "step": "mfa", "ok": True, "bypassed": True, "ts": 1030},
        {"app": "wiki", "step": "token", "ok": False, "ts": 1080},
    ],
}))

# 3. 同一账号再次失败，连续失败间隔一并记录
print(login({
    "user": "bob",
    "events": [{"app": "vpn", "step": "password", "ok": False, "ts": 1042}],
}))

# 4. 运维视角汇总：按环节归类的原因、MFA 统计、按应用/按天失败数、间隔
import json
print(json.dumps(report(), indent=2, ensure_ascii=False))

# 5. 异常登录按账号、应用、时间筛选回看
print(report(user="carol", app="vpn", since=1020))
