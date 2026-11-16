from ssoflow import login, report

print(login({"user": "bob", "events": [{"app": "mail", "step": "password", "ok": True}, {"app": "mail", "step": "mfa", "ok": False}]}))
print(login({"user": "carol", "events": [{"app": "mail", "step": "password", "ok": True}, {"app": "vpn", "step": "mfa", "ok": True}]}))
print(report())

# TODO: 第二段演示（实现后补）
