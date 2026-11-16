from weakpass import attempt, report

print(attempt({"user": "alice", "dictionary": ["123456", "s3cret"], "valid": "s3cret"}))
print(attempt({"user": "svc", "locked": True, "dictionary": ["a", "b"], "valid": "b"}))
print(report())

# TODO: 第二段演示（实现后补）
