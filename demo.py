from packerdetect import detect, diff, families

SAMPLE = {
    "name": "drop.exe",
    "sections": ["UPX0", "UPX1", ".text"],
    "entry": "0x401000",
    "imports": ["LoadLibraryA"],
}

RENAMED = {"name": "drop2.exe", "sections": [".x1", ".x2"], "entry": "0x401000"}

print("已知家族", sorted(families()))
print("判定结果", detect(SAMPLE)["packer"], detect(SAMPLE)["evidence"])
print("改名区段", detect(RENAMED)["packer"])
print("差异项", diff(SAMPLE, RENAMED)["section_removed"])

# TODO: 第二段演示（实现后补）
