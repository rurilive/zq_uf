from loginflow import Step, run

STEPS = [lambda ctx: {"token": "abc"}, lambda ctx: {"logged": bool(ctx["token"])}]
print(run("acme", STEPS))

# 第二段：三个账号的可编排登录（步骤故意乱序声明）
def make_steps():
    return [
        Step("sms", lambda ctx: {"logged": bool(ctx.get("captcha_code"))},
             expect="logged", needs=("token",), provides=("logged",),
             captcha=True, retries=1),
        Step("password", lambda ctx: {"token": "tok-" + ctx["account"]},
             expect="token", needs=("page",), provides=("token",)),
        Step("open", lambda ctx: {"page": "ok"},
             expect="page", provides=("page",)),
    ]


CODES = {"alice": "111111", "bob": "222222", "carol": "333333"}
for account in CODES:
    result = run(account, make_steps(),
                 callbacks={"captcha": lambda ch: CODES[ch["account"]]},
                 session_dir="sessions")
    for s in result.steps:
        print("%s %-8s ok=%s %.3fs retries=%d %s"
              % (account, s.name, s.ok, s.duration, s.retries, s.reason))
    print(account, "session:", result.session_file)
