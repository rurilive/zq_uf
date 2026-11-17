from authmatrix import expired_credential, matrix, report

# 演示 1：一个接口把四种身份都跑完，他人凭证发生了绕过。
print(matrix({
    "api": "/admin/users",
    "expected": "deny",
    "anonymous": "deny",
    "expired": {"actual": "deny", "validity": 900},
    "other_user": {"actual": "allow"},
    "low_privilege": {"status": "skip", "reason": "no test account"},
}))

# 演示 2：只补跑上次缺的组合，然后看按接口汇总的覆盖与回归差异。
print(matrix({"api": "/admin/orders", "anonymous": "deny"}))
before = report()
print("coverage:", before["coverage"], "missing:", before["missing"])

matrix({"api": "/admin/orders", "expected": "deny", "cases": {
    "anonymous": "deny",
    "expired": {"actual": "deny", "validity": 900},
    "other_user": "deny",
    "low_privilege": "deny",
}})
after = report()
print("diff:", after["diff"])
print("failed:", after["failed"])
print("expired not tested:", after["expired"]["not_tested"])

# 过期凭证按实际有效期生成，过期时刻与核查时刻都留档。
print(expired_credential(900, issued_at="2026-01-01T00:00:00Z",
                         now="2026-01-01T01:00:00Z"))
