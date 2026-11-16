import time

from authmatrix import matrix, report

NOW = int(time.time())

# 第一轮：四个接口按四种身份跑组合
print(matrix({
    "api": "/admin/users",
    "expected": "deny",
    "now": NOW,
    "results": {
        "anonymous": "deny",
        "expired": {"actual": "deny", "expires_at": NOW - 3600},
        "other_user": "allow",
        "low_privilege": {"skip": "no low-privilege account"},
    },
}))
print(matrix({
    "api": "/admin/orders",
    "expected": "deny",
    "now": NOW,
    "results": {
        "anonymous": "deny",
        "expired": {"actual": "deny", "expires_at": NOW + 3600},  # 凭证未真过期，记未测到
    },
}))

# 第二轮：回归只补跑未通过和新增组合，已通过的记跳过
print(matrix({
    "api": "/admin/users",
    "expected": "deny",
    "now": NOW,
    "results": {
        "anonymous": "deny",
        "expired": {"actual": "deny", "expires_at": NOW - 3600},
        "other_user": "deny",  # 上次未通过，这次修复
        "low_privilege": "deny",  # 新增补跑
    },
}))

print(report())
