from txrollback import STORE, commit, reset

reset()
try:
    commit({"id": "b1", "rows": [{"key": "k1"}, {"key": "k2", "fail": True}]})
except RuntimeError as exc:
    print("失败", exc)
print("库中仍有", sorted(STORE))

# TODO: 第二段演示（实现后补）