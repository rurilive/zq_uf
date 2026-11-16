from versionid import identify, register, report

PORT_80 = "HTTP/1.1 200 OK\r\nServer: nginx/1.18.0\r\n\r\n"
PORT_8080 = "HTTP/1.1 200 OK\r\nServer: nginx/1.22.1\r\n\r\n"
NO_HEADER = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<title>router</title>"

register("10.0.0.8", 80, PORT_80)
register("10.0.0.8", 8080, PORT_8080)
register("10.0.0.9", 81, NO_HEADER)

print(identify("10.0.0.8", 80))
print(identify("10.0.0.9", 81))
print(report())

# TODO: 第二段演示（实现后补）
