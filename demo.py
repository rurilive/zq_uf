from langdetect2 import confident, detect

print(detect({"headers": {"content-language": "ja"}}))
print(detect({}))
print(confident({}))

# TODO: 第二段演示（实现后补）
