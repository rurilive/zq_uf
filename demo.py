from vulnretest import history, retest

print(retest({"id": "V-7", "project": "shop", "steps": [{"name": "回显", "ok": True}]}))
print(retest({"id": "V-7", "project": "shop", "steps": [{"name": "回显", "ok": False}]}))
print(history("V-7")["records"])

# TODO: 第二段演示（实现后补）
