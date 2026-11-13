"""按可接入、需登录、需人工三类打分并给出置信度与证据。"""

ACCESSIBLE = "accessible"
LOGIN_REQUIRED = "login_required"
MANUAL_REVIEW = "manual_review"


def classify(page_records, signals, pagination, garbled_pages):
    """返回分数、分类、置信度、扣分证据。"""
    score = 100
    deductions = []

    timeout_pages = [record["url"] for record in page_records if record.get("timed_out")]
    if timeout_pages:
        penalty = min(45, 15 * len(timeout_pages))
        score -= penalty
        deductions.append({"points": penalty, "reason": "请求超时", "evidence": timeout_pages})

    error_pages = [record["url"] for record in page_records if record["status"] == 0 and not record.get("timed_out")]
    if error_pages:
        penalty = min(45, 15 * len(error_pages))
        score -= penalty
        deductions.append({"points": penalty, "reason": "连接失败", "evidence": error_pages})

    bad_status = [record["url"] for record in page_records if record["status"] not in (0, 200)]
    if bad_status:
        penalty = min(30, 10 * len(bad_status))
        score -= penalty
        deductions.append({"points": penalty, "reason": "非 200 状态码", "evidence": bad_status})

    long_redirects = [record["url"] for record in page_records if len(record.get("redirects", [])) >= 3]
    if long_redirects:
        score -= 5
        deductions.append({"points": 5, "reason": "重定向链过长", "evidence": long_redirects})

    if garbled_pages:
        score -= 20
        deductions.append({"points": 20, "reason": "存在乱码页面", "evidence": garbled_pages})

    if pagination["style"] == "none":
        score -= 10
        deductions.append({"points": 10, "reason": "未推断出分页规则", "evidence": []})

    if signals["login_required"]:
        score -= 40
        deductions.append({"points": 40, "reason": "检测到登录要求", "evidence": signals["login_required"]})
    if signals["captcha"]:
        score -= 25
        deductions.append({"points": 25, "reason": "检测到验证码", "evidence": signals["captcha"]})
    if signals["signature"]:
        score -= 20
        deductions.append({"points": 20, "reason": "检测到接口签名特征", "evidence": signals["signature"]})

    score = max(0, score)

    if signals["login_required"]:
        classification = LOGIN_REQUIRED
    elif signals["captcha"] or signals["signature"] or garbled_pages or score < 60:
        classification = MANUAL_REVIEW
    else:
        classification = ACCESSIBLE

    confidence, confidence_label = _confidence(page_records, signals, pagination, garbled_pages)
    evidence = []
    for group in signals.values():
        evidence.extend(group)
    evidence.extend(pagination.get("evidence", []))
    return {
        "score": score,
        "classification": classification,
        "confidence": confidence,
        "confidence_label": confidence_label,
        "deductions": deductions,
        "evidence": evidence,
    }


def _confidence(page_records, signals, pagination, garbled_pages):
    ok_pages = [record for record in page_records if record["status"] == 200 and not record.get("timed_out")]
    coverage = len(ok_pages) / max(1, len(page_records))
    signal_hits = sum(1 for group in signals.values() if group)
    signal_weight = min(0.2, 0.07 * signal_hits)
    pagination_weight = 0.1 if pagination["style"] != "none" else 0.0
    garble_weight = 0.1 if garbled_pages else 0.0
    value = round(min(0.98, 0.5 * coverage + 0.2 + signal_weight + pagination_weight + garble_weight), 2)
    if value >= 0.85:
        label = "high"
    elif value >= 0.6:
        label = "medium"
    else:
        label = "low"
    return value, label
