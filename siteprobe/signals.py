"""登录跳转、验证码、接口签名三类接入风险特征识别。"""

import re
from urllib.parse import parse_qsl, urlparse

LOGIN_KEYWORDS = ("login", "signin", "sign-in", "auth", "sso", "passport", "oauth")
LOGIN_ZH = ("登录", "请先登录", "未登录", "登陆")
CAPTCHA_KEYWORDS = ("captcha", "recaptcha", "hcaptcha", "geetest", "验证码", "滑块", "拼图")
SIGNATURE_KEYS = ("sign", "signature", "sig", "token", "_ts", "timestamp", "nonce", "appkey", "app_key")
CAPTCHA_FORM_RE = re.compile(r"<img[^>]+(id|name|src)=['\"]?[^'\"\s>]*captcha", re.IGNORECASE)


def scan_signals(home_url, page_records):
    """汇总所有页面的风险特征，返回命中证据与计数。"""
    found = {"login_required": [], "captcha": [], "signature": []}
    home_host = urlparse(home_url).netloc

    for record in page_records:
        url = record["url"]
        text = record.get("text", "")
        lowered_text = (text or "").lower()
        final_url = record.get("final_url", "")
        final_path = urlparse(final_url).path.lower()

        redirect_host = urlparse(final_url).netloc
        login_redirect = bool(
            record.get("redirects")
            and (
                any(keyword in final_path for keyword in LOGIN_KEYWORDS)
                or (redirect_host != home_host and _has_login_word(final_url))
            )
        )
        if login_redirect:
            found["login_required"].append("%s 重定向到登录页 %s" % (url, final_url))
        elif any(word in lowered_text for word in LOGIN_ZH):
            found["login_required"].append("%s 页面包含登录提示文案" % url)
        elif any(keyword in final_path for keyword in LOGIN_KEYWORDS):
            found["login_required"].append("%s 最终地址命中登录路径 %s" % (url, final_path))

        matched = [keyword for keyword in CAPTCHA_KEYWORDS if keyword in lowered_text]
        has_image = bool(CAPTCHA_FORM_RE.search(text or ""))
        if has_image and "captcha-img" not in matched:
            matched.append("captcha-img")
        if matched:
            found["captcha"].append("%s 出现验证码标记: %s" % (url, ",".join(matched[:3])))

        query_keys = [key.lower() for key, _ in parse_qsl(urlparse(url).query)]
        if any(key in SIGNATURE_KEYS for key in query_keys):
            found["signature"].append("%s 查询参数含签名/令牌字段" % url)
        for anchor in record.get("anchors", []):
            anchor_href = anchor["href"] if isinstance(anchor, dict) else anchor.href
            anchor_keys = [key.lower() for key, _ in parse_qsl(urlparse(anchor_href).query)]
            if any(key in SIGNATURE_KEYS for key in anchor_keys):
                found["signature"].append("%s 页面链接含签名/令牌字段" % url)
                break
        if re.search(r"[\"'](sign|signature|appKey|app_key|nonce)[\"']\s*:", text or ""):
            found["signature"].append("%s 页面脚本出现签名参数赋值" % url)

    for key in found:
        found[key] = sorted(set(found[key]))
    return found


def _has_login_word(url):
    lowered = url.lower()
    return any(keyword in lowered for keyword in LOGIN_KEYWORDS)
