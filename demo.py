from weakpass import attempt, report, reset

reset()
print(attempt({"user": "alice", "system": "mail", "dictionary": ["123456", "s3cret"], "valid": "s3cret"}))
print(attempt({"user": "svc", "system": "vpn", "locked": True, "dictionary": ["a", "b"], "valid": "b", "now": 100}))
print(report())

# 第二段演示：锁定中途触发，恢复后从停下的口令继续
reset()
print(attempt({"user": "bob", "system": "vpn", "dictionary": ["a", "b", "c", "d"],
               "valid": "d", "lock_after": 2, "lock_duration": 60, "now": 100}))
print(attempt({"user": "bob", "valid": "d", "now": 200}))
print(report(system="vpn"))
