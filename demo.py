from timeoutgov import run, set_clock

now = [0]
set_clock(lambda: now[0])
now[0] = 10
print(run({"name": "job", "work": lambda: "done"}))
now[0] = 45
try:
    run({"name": "slow", "work": lambda: "late"})
except TimeoutError as exc:
    print("超时", exc)

# TODO: 第二段演示（实现后补）