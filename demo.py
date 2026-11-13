"""两步表单发现演示：登录页提交后跳转到资料页。"""

import json

from formdiscover import discover

step2 = {
    "action": "/profile",
    "method": "POST",
    "inputs": [
        {"name": "nickname", "type": "text", "required": True},
        {"name": "age", "type": "number"},
        {"name": "otp_token", "type": "hidden", "value": "n9"},
        {"name": "save", "type": "submit"},
    ],
}

step1 = {
    "action": "/login",
    "method": "POST",
    "inputs": [
        {"name": "user", "type": "text"},
        {"name": "pwd", "type": "password"},
        {"name": "csrf", "type": "hidden", "value": "t1"},
        {"name": "go", "type": "submit"},
    ],
    "next": step2,
}

result = discover(step1)
print(json.dumps(result, ensure_ascii=False, indent=2))

assert [s["action"] for s in result["steps"]] == ["/login", "/profile"]
assert [f["name"] for f in result["fields"]] == ["user", "pwd"]
assert [f["name"] for f in result["hidden"]] == ["csrf"]
assert result["tokens"][0]["token_kind"] == "csrf"
assert result["steps"][1]["tokens"][0]["token_kind"] == "onetime"
assert result["steps"][1]["fields"][0]["required"] is True
print("demo assertions passed")
