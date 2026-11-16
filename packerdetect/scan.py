"""按区段名判断样本是否加壳。"""

_KNOWN = {
    "upx0": "upx",
    "upx1": "upx",
    ".aspack": "aspack",
    ".adata": "aspack",
    ".themida": "themida",
    ".petite": "petite",
}


def families():
    """返回已知家族和它们的区段特征。"""
    return {"upx": ["upx0", "upx1"], "aspack": [".aspack", ".adata"], "themida": [".themida"]}


def detect(sample):
    """只看区段名，认不出就当成没加壳。"""
    if not isinstance(sample, dict):
        raise ValueError("sample 必须是字典")
    sections = sample.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("样本缺少区段信息")
    names = [str(name).lower() for name in sections]
    hits = [name for name in names if name in _KNOWN]
    packer = _KNOWN[hits[0]] if hits else "unpacked"
    return {
        "sample": sample.get("name", ""),
        "packer": packer,
        "evidence": ["section:%s" % name for name in hits],
        "unpacked": None,
        "pending": False,
    }


def diff(before, after):
    """只比区段名，导入表差异留空。"""
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("样本必须是字典")
    old = [str(name).lower() for name in before.get("sections", [])]
    new = [str(name).lower() for name in after.get("sections", [])]
    added = [name for name in new if name not in old]
    removed = [name for name in old if name not in new]
    return {
        "sample": after.get("name") or before.get("name", ""),
        "section_added": added,
        "section_removed": removed,
        "import_delta": [],
        "changed": bool(added or removed),
    }
