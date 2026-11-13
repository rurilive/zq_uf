import os
import tempfile

from replayfail import record, replay

SAMPLES = [
    {
        "method": "POST",
        "url": "http://site/orders",
        "headers": {"Authorization": "Bearer sig-1", "Content-Type": "application/json"},
        "body": '{"amount": 100}',
        "error_type": "SignatureMismatch",
        "response": {"status": 403, "headers": {"X-Trace": "t1"}, "body_summary": "bad signature"},
    },
    {
        "method": "POST",
        "url": "http://site/pay",
        "headers": {"Authorization": "Bearer sig-2"},
        "body": '{"amount": 200}',
        "error_type": "SignatureMismatch",
        "response": {"status": 403, "headers": {}, "body_summary": "bad signature"},
    },
    {
        "method": "GET",
        "url": "http://site/timeout",
        "headers": {"Authorization": "Bearer sig-3"},
        "body": "",
        "error_type": "ConnectionReset",
        "response": {"status": None, "headers": {}, "body_summary": "connection reset"},
    },
    {
        "method": "GET",
        "url": "http://site/profile",
        "headers": {"Authorization": "Bearer sig-4"},
        "body": "",
        "error_type": "ConnectionReset",
        "response": {"status": None, "headers": {}, "body_summary": "connection reset"},
    },
]


def fetch(request):
    """模拟修复后的服务端：按 URL 给出成功、原错误或新错误。"""
    url = request["url"]
    if url.endswith(("/orders", "/profile")):
        return None
    if url.endswith("/timeout"):
        return "TimeoutError"
    return "SignatureMismatch"


with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "failures.jsonl")
    drill_db = os.path.join(tmp, "drill.sqlite3")
    for failure in SAMPLES:
        record(failure, path)

    first = replay([path], fetch, drill_db=drill_db)
    second = replay([path], fetch, drill_db=drill_db)
    print("first replay :", first)
    print("second replay:", second)
