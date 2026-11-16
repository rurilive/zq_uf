from keyleak import report, scan

PATHS = {
    "web/app.py": 'password = "hunter2"\ntoken = "samevalue"\n',
    "ops/deploy.yml": "api_key = samevalue\n",
    "web/fixture.py": 'blob = "aB3xK9mQ2pL7vN4rT8wZ"\n',
}

result = scan(PATHS)
print(result["entries"])
print(result["repos"])
print(report())

# TODO: 第二段演示（实现后补）
