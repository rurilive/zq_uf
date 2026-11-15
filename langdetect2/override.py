"""人工复核覆盖。

覆盖只改"最终采用值"，原识别结果完整保留，便于回溯与统计错判来源。
低把握页面必须人工覆盖后才能入库。
"""

from .detector import detect


def apply_override(result, language, reviewer=None, note=None):
    """在识别结果上记录人工覆盖，返回带 override 字段的新结果。"""
    overridden = dict(result)
    overridden["override"] = {
        "original_language": result["language"],
        "original_confidence": result["confidence"],
        "language": language,
        "reviewer": reviewer,
        "note": note,
    }
    # 最终分流以覆盖后的语言为准
    overridden["language"] = language
    overridden["confidence"] = "high"
    overridden["confidence_reason"] = "human-override"
    return overridden


def review_page(page, language, reviewer=None, note=None):
    """对一个低把握页面执行人工复核：先识别，再覆盖。"""
    return apply_override(detect(page), language, reviewer=reviewer, note=note)


def is_indexable(result):
    """入库判定：高把握直接入；人工覆盖后可入；低把握且未覆盖不入。"""
    if "override" in result:
        return True
    return result["confidence"] == "high"
