from versionid import identify, override, register, report

PORT_80 = "HTTP/1.1 200 OK\r\nServer: nginx/1.18.0\r\n\r\n"
PORT_8080 = "HTTP/1.1 200 OK\r\nServer: nginx/1.22.1\r\n\r\n"
NO_HEADER = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<title>router</title>"
STRIPPED_HEADER = (
    "HTTP/1.1 200 OK\r\nServer: web\r\nContent-Type: text/html\r\n\r\n"
    "<html><title>Welcome to nginx!</title></html>"
)
SSH_BANNER = "SSH-2.0-OpenSSH_7.4\r\n"

register("10.0.0.8", 80, PORT_80)
register("10.0.0.8", 8080, PORT_8080)
register("10.0.0.9", 81, NO_HEADER)

print(identify("10.0.0.8", 80))
print(identify("10.0.0.9", 81))
print(report())

# 第二段：改过的头靠默认页兜底，协议横幅独立识别，人工推翻后未知清单清空
register("10.0.0.10", 443, STRIPPED_HEADER)
register("10.0.0.10", 22, SSH_BANNER)
print(identify("10.0.0.10", 443)["service"], identify("10.0.0.10", 443)["confidence"])
print(identify("10.0.0.10", 22)["service"], identify("10.0.0.10", 22)["version"])

override("10.0.0.9", 81, "openwrt", "22.03", note="人工确认是路由器")
result = report()
print(result["hosts"]["10.0.0.8"])   # 两个端口各自一条，不合并
print(result["unknown"])             # 人工确认后不再欠补采
print(result["distribution"])        # 运营按服务看版本分布
