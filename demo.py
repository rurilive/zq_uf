from sbomlayers import report, sbom

# 第一段：同镜像两次扫描。基础层升级后旧组件被删除/替换，diff 按组件给出变化。
BASE = {
    "image": "app:1",
    "digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "scanned_at": "2026-01-01T00:00:00+00:00",
    "layers": [
        {"id": "l1", "add": [
            {"name": "openssl", "version": "1.1", "license": "OpenSSL"},
            {"name": "curl", "version": "7.0", "license": "curl"},
            {"name": "zlib", "version": "1.3", "license": "Zlib"},
        ]},
        {"id": "l2", "add": [{"name": "openssl", "version": "1.1"}]},
    ],
}
print("--- 首次扫描 app:1 ---")
print(sbom(BASE))

UPGRADED = {
    "image": "app:1",
    "digest": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
    "scanned_at": "2026-02-01T00:00:00+00:00",
    "layers": [
        {"id": "l1", "add": [
            {"name": "openssl", "version": "3.0", "license": "Apache-2.0"},
            {"name": "zlib", "version": "1.3", "license": "Zlib"},
            {"name": "libc", "version": "2.0", "license": "LGPL"},
        ]},
    ],
}
print("--- 基础层升级后再次扫描 app:1，diff ---")
print(sbom(UPGRADED)["diff"])

# 第二段：两个镜像整体对比，新增/删除/版本变化分开统计。
sbom({
    "image": "app:2",
    "digest": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
    "scanned_at": "2026-02-02T00:00:00+00:00",
    "layers": [
        {"id": "l1", "add": [
            {"name": "openssl", "version": "3.0", "license": "Apache-2.0"},
            {"name": "zlib", "version": "1.4", "license": "Zlib"},
            {"name": "bash", "version": "5.2", "license": "GPL-3.0"},
        ]},
    ],
})
print("--- app:1 vs app:2 ---")
print(report("app:1", "app:2"))

print("--- 按镜像汇总 ---")
print(report())
