from crawldiagnose import capture, diagnose

cid = capture({"method": "GET", "url": "http://shop/1", "status": 500, "response": "timeout"})
print(cid, diagnose(cid))

# TODO: 第二段演示（实现后补）
