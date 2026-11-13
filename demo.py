from retryall import call

state = {"n": 0}


def flaky():
    state["n"] += 1
    if state["n"] < 3:
        raise ValueError("boom")
    return "ok"


print(call(flaky, {"attempts": 3}))

# TODO: 第二段演示（实现后补）
