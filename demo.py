import tempfile

from pagesnap import save

with tempfile.TemporaryDirectory() as tmp:
    print(save({"id": "p1", "text": "页面正文"}, tmp))

# TODO: 第二段演示（实现后补）
