from sitepolicy import audit, resolve, update

# 三层继承：default -> group:news -> site:shop
update({"retry": 1}, operator="ops")
update({
    "groups": {"news": {"rate": 3, "timeout": 10}},
    "sites": {"shop": {"group": "news", "rate": 5}},
}, operator="alice")

policy = resolve("shop")
print("policy:", dict(policy))
print("conflicts:", policy.conflicts)

# 失败更新：校验不通过，保留旧快照
try:
    update({"rate": -1}, operator="bad-op")
except ValueError as exc:
    print("rejected:", exc)
print("after rollback:", dict(resolve("shop")))

print("audit:")
for entry in audit():
    print(" ", entry)
