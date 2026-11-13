from formdiscover import discover

page = {
    "action": "/login",
    "method": "POST",
    "inputs": [
        {"name": "user", "type": "text"},
        {"name": "csrf", "type": "hidden", "value": "abc"},
    ],
}

print(discover(page))

# TODO: 第二段演示（实现后补）
