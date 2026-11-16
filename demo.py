from auditlog import append, tail, verify

DAY1 = {"day": "day-01", "action": "login", "user": "alice"}
DAY2 = {"day": "day-02", "action": "read", "user": "bob"}

first = append(DAY1)
second = append(DAY2)
print("seq", first["seq"], "link", repr(first["link"]))
print("digest", first["digest"], second["digest"])
print("tail", tail()["seq"], tail()["digest"] == second["digest"])
print(verify((1, 1)))

# TODO: 第二段演示（实现后补）
