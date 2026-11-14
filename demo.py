from wmsync import update, watermark

update("source-a", 100)
update("source-b", 40)
print(watermark())

# TODO: 第二段演示（实现后补）
