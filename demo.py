from contenttype import dispatch, parse

RESPONSES = [
    ("JSON 接口", {"url": "http://a/api/user", "headers": {"Content-Type": "application/json"},
                  "body": '{"id": 1, "name": "uf"}'}),
    ("XML 接口（缺头）", {"url": "http://a/feed", "body": '<?xml version="1.0"?><rss><item/></rss>'}),
    ("HTML 页面", {"url": "http://a/index", "headers": {"Content-Type": "text/html"},
                  "body": "<!DOCTYPE html><html><body>hi</body></html>"}),
    ("错误页", {"url": "http://a/x", "status": 500, "headers": {"Content-Type": "text/html"},
               "body": "<html><title>500 Internal Server Error</title></html>"}),
    ("二进制附件", {"url": "http://a/logo.png", "headers": {"Content-Length": "24"},
                  "body": b"\x89PNG\r\n\x1a\n" + b"\x00" * 16}),
]

for label, resp in RESPONSES:
    det = dispatch(resp)
    print("%s -> type=%s confidence=%.2f handler=%s" % (
        label, det.type, det.confidence, det.handler))
    print("    sample=%r" % det.sample)
    result = parse(resp)
    if isinstance(result, bytes):
        result = result[:40]
    print("    parse=%r" % (result,))
