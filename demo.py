from compressneg import decode, headers

print(headers("shop"))
print(decode({"body": "正文".encode("utf-8")}))

# TODO: 第二段演示（实现后补）
