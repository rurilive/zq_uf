"""演示：图形码人工回写继续，短信码无人处理超时失败。"""

from captchahandoff import (
    CaptchaRequired,
    CaptchaTimeout,
    challenge,
    reset,
    submit,
    ticket_status,
    wait,
)


def hit(account, kind, ttl=None):
    try:
        challenge(account, kind=kind, ttl=ttl)
    except CaptchaRequired as exc:
        print("命中验证码", exc.account, exc.kind, "工单", exc.ticket_id)
        return exc.ticket_id
    return None


def main():
    reset()

    # 图形码：人工提交后回写继续
    ticket = hit("acc-1", "image")
    again = hit("acc-1", "image")
    print("账号互斥 acc-1 同工单", ticket == again)
    result = submit("acc-1", "8f3k")
    print("回写继续", result["account"], result["status"], "code", result["code"])

    # 短信码：无人处理，超时失败并释放账号
    ticket2 = hit("acc-2", "sms", ttl=0.2)
    try:
        wait("acc-2", poll_interval=0.05)
    except CaptchaTimeout as exc:
        print("超时失败", exc.account, "工单", exc.ticket_id, ticket_status(exc.ticket_id)["status"])
    retry = hit("acc-2", "sms")
    print("释放重试 acc-2 新工单", retry != ticket2)


if __name__ == "__main__":
    main()
