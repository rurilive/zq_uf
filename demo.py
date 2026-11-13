from crawldiagnose import capture, diagnose

REQUESTS = [
    # 请求一致但响应变化：页面结构可能改版
    {"method": "GET", "url": "http://shop/list?page=1&ts=111&sign=aaa",
     "headers": {"Cookie": "sid=1"}, "status": 200, "response": "<ul>v1</ul>",
     "replay": {"request": {"url": "http://shop/list?page=1&ts=222&sign=bbb",
                            "headers": {"Cookie": "sid=2"}},
                "response": "<ul>v2</ul>"}},
    # 请求变化：重放时表单多了 channel 字段
    {"method": "POST", "url": "http://shop/pay", "headers": {"Cookie": "sid=1"},
     "body": "amount=1&sign=old", "status": 200, "response": "ok",
     "replay": {"request": {"body": "amount=1&sign=new&channel=app"}}},
    # 状态变化：登录态失效
    {"method": "GET", "url": "http://shop/user", "status": 200, "response": "{}",
     "replay": {"status": 401, "response": "unauthorized"}},
]

for req in REQUESTS:
    cid = capture(req)
    report = diagnose(cid)
    print("[%s] %s %s -> %s" % (report["id"], report["method"],
                                report["url"], report["category"]))
    for key in ("request_diff", "status_diff", "response_diff"):
        if report[key]:
            print("  %s: %s" % (key, report[key]))
    for hint in report["hints"]:
        print("  hint: %s" % hint)
