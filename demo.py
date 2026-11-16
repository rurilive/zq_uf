import json

from packerdetect import (
    detect,
    diff,
    families,
    get_unpack_params,
    set_unpack_params,
    unpack,
)

SAMPLE = {
    "name": "drop.exe",
    "sections": ["UPX0", "UPX1", ".text"],
    "entry": "0x401000",
    "imports": ["LoadLibraryA"],
}

RENAMED = {"name": "drop2.exe", "sections": [".x1", ".x2"], "entry": "0x401000"}

print("== 基础入口（保持兼容） ==")
print("已知家族", sorted(families()))
print("判定结果", detect(SAMPLE)["packer"], detect(SAMPLE)["evidence"])
print("改名区段", detect(RENAMED)["packer"], detect(RENAMED)["pending"])
print("差异项", diff(SAMPLE, RENAMED)["section_removed"])

# 带完整结构/行为信息的样本：区段名已被改掉，但结构特征还在
upx_renamed = {
    "name": "renamed_upx.exe",
    "sections": [
        {"name": ".x1", "characteristics": "RW", "raw_size": 0, "virtual_size": 0x1000,
         "virtual_address": "0x401000"},
        {"name": ".x2", "characteristics": "RWX", "entropy": 7.8, "raw_size": 0x800,
         "virtual_size": 0x1000, "virtual_address": "0x402000"},
    ],
    "entry": "0x402010",
    "imports": ["LoadLibraryA", "GetProcAddress"],
    "behavior": ["alloc_rwx"],
    "hidden_imports": ["CreateFileA", "WriteFile"],
    "payload": {
        "sections": [".text", ".rdata", ".data"],
        "entry": "0x401000",
        "imports": ["CreateFileA", "WriteFile"],
        "behavior": [],
    },
}

unknown_packed = {
    "name": "mystery.exe",
    "sections": [
        {"name": ".text", "characteristics": "RX", "entropy": 6.1},
        {"name": ".blob", "characteristics": "RWX", "entropy": 7.9},
    ],
    "entry": "0x403020",
    "imports": ["LoadLibraryA"],
    "behavior": ["self_modify", "dynamic_api_resolve"],
}

themida_sample = {
    "name": "hard.exe",
    "sections": [{"name": ".themida", "characteristics": "RWX", "entropy": 7.9}],
    "entry": "0x405000",
    "imports": [],
    "behavior": ["anti_debug", "anti_vm"],
}

print()
print("== 结构+行为识别（不靠区段名） ==")
out = detect(upx_renamed)
print("改名 UPX：", out["packer"], out["confidence"], "待人工=", out["pending"])
for item in out["evidence"]:
    print("  依据", item)

out = detect(unknown_packed)
print("未知壳：", out["packer"], "待人工=", out["pending"])
print("  结构画像", json.dumps(out["structural"], ensure_ascii=False))

print()
print("== 脱壳与前后对照 ==")
res = unpack(upx_renamed)
print("脱壳 ok=", res["ok"], "使用参数=", res["params"]["tool"])
print("  差异项", res["diff"]["section_added"], res["diff"]["section_removed"])
print("  导入表变化", res["diff"]["import_delta"])
print("  脱后复检", res["diff"]["recheck"]["packer"], "干净=", res["clean"])

print()
print("== 脱壳失败：保留原样本和原因 ==")
# 显式给不带绕过的参数，保证失败场景可复现（不依赖全局参数库状态）
res = unpack(themida_sample, {"tool": "manual+x64dbg", "method": "dynamic_unpack", "bypass": []})
print("默认参数 ok=", res["ok"], "原因=", res["reason"], res["reason_detail"])
print("  原样本已保留=", res["original"]["name"])

# 同家族复用：补上绕过参数再脱，参数按家族留存供后续样本复用
set_unpack_params("themida", {"bypass": ["anti_debug", "anti_vm"]})
print("家族参数已更新，bypass=", get_unpack_params("themida")["bypass"])
retry = unpack(themida_sample)
print("复用新参数再脱 ok=", retry["ok"], "干净=", retry.get("clean"))

print()
print("== 未知家族不给参数不硬脱 ==")
res = unpack(unknown_packed)
print("ok=", res["ok"], "原因=", res["reason"], "-", res["reason_detail"])
