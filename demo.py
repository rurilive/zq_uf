import json

from formstate import start, step

state = start("login")
step(state, {"user": "a"})
step(state, {"user": "a"})
print(state)

# 第二段：多步表单，模拟中途失败、回退与断点恢复
form = {
    "name": "signup",
    "steps": [
        {"name": "account", "page": "<p>account</p>"},
        {"name": "captcha", "type": "captcha", "timeout": 60, "page": "<p>captcha</p>"},
        {"name": "profile", "page": "<p>profile</p>", "fail_times": 1},
    ],
}
state = start(form)
step(state, {"user": "a"})       # account 提交
step(state, {"user": "a"})       # 重复提交 -> 幂等命中，不再提交
step(state, {})                  # 验证码步骤 -> 挂起等待回填
step(state, {"captcha": "9f3"})  # 外部回填 -> 通过
step(state, {"nick": "x"})       # profile 第一次失败 -> 回退到 captcha
step(state, {"captcha": "9f3"})  # 幂等命中，直接回到断点
step(state, {"nick": "x"})       # 重试成功 -> done
for entry in state["trail"]:
    print(json.dumps(entry, ensure_ascii=False))

