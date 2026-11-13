from captchahandoff import CaptchaRequired, challenge

for account in ("acc-1", "acc-2"):
    try:
        challenge(account)
    except CaptchaRequired as exc:
        print("命中验证码", exc.account, exc.kind)

# TODO: 第二段演示（实现后补）
