from sbomlayers import report, sbom

print(sbom({
    "image": "app:1",
    "layers": [
        {"id": "l1", "add": [{"name": "openssl", "version": "1.1"}]},
        {"id": "l2", "add": [{"name": "openssl", "version": "1.1"}], "remove": ["curl"]},
    ],
}))
print(report())

# TODO: 第二段演示（实现后补）
