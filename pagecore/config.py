"""按站点可覆盖的抽取规则。

规则集中放在这里，页面改版时只调整配置（或传入 overrides），
不需要改抽取算法。站点按域名注册，支持子域名回退匹配。
"""
import copy
import json
import re

DEFAULT_CONFIG = {
    # 整棵子树都忽略的标签
    "strip_tags": [
        "head", "title", "script", "style", "noscript", "template", "iframe",
        "svg", "canvas", "object", "embed", "form", "button", "input",
        "select", "textarea",
    ],
    # 这些标签整体视为站点框架
    "noise_tags": ["nav", "aside", "footer", "header", "menu", "dialog"],
    # class/id/role/itemprop 里出现这些词，视为噪声容器
    "noise_hints": [
        "nav", "navbar", "navigation", "menu", "menubar", "breadcrumb",
        "breadcrumbs", "sidebar", "comment", "comments", "disqus", "discuss",
        "reply", "replies", "recommend", "recommended", "recommendation",
        "related", "recirc",
        "share", "sharing", "social", "ad", "ads", "advert", "advertisement",
        "sponsor", "sponsored", "promo", "promotion", "banner", "popup",
        "modal", "cookie", "newsletter", "subscribe", "subscription",
        "pagination", "pager", "prevnext", "hot", "popular", "trending",
        "rank", "ranking", "taglist", "tags", "byline", "author", "toolbar",
        "widget", "rss", "search", "login", "signup", "register",
        "推荐", "评论", "导航", "广告", "相关", "热门", "版权", "分享",
        "登录", "注册", "搜索",
    ],
    # 这些标签说明里面是正文区域
    "content_tags": ["article", "main"],
    # 这些标签内部的 header/footer/aside 属于文章自身结构，不按框架剔除
    "article_tags": ["article"],
    # class/id/role/itemprop 里出现这些词，说明是正文容器
    "content_hints": [
        "article", "content", "post", "entry", "story", "detail", "hentry",
        "articlebody", "正文",
    ],
    # 块得分达到该值才在第一轮被当成正文
    "keep_score": 0.35,
    # 非正文容器里的块，至少这么长才参与打分保留
    "min_block_chars": 10,
    # 正文短于这个长度、且块数不多时进入“短正文”模式，低分块也保留
    "short_body_chars": 160,
    # 短正文模式最多容忍的块数：块多说明是正常文章，只是每段较短
    "short_max_blocks": 4,
    # 短正文模式下，长度达到这个值即可保留
    "short_min_chars": 2,
    # 链接文字占比达到该值视为导航/列表块
    "max_link_ratio": 0.5,
    # 位于正文容器内的加分
    "content_boost": 0.10,
    # 上次正文保留天数（PageStore 使用）
    "retention_days": 7,
}

# 站点覆盖：{域名: {配置项: 值}}
# 只写需要改的项，例如：
# SITE_CONFIGS["news.example.com"] = {"noise_hints": ["推荐", "热门"]}
SITE_CONFIGS = {}

_HINT_CJK = re.compile(r"[一-鿿]")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def normalize_site(site):
    """把 URL / 主机名 / 带端口的主机名统一成域名。"""
    if not site or not isinstance(site, str):
        return None
    value = site.strip().lower()
    value = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value)
    value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    value = value.rsplit("@", 1)[-1]
    value = value.split(":", 1)[0]
    if value.startswith("www."):
        value = value[4:]
    return value.rstrip(".") or None


def _copy_config(source):
    return {key: copy.deepcopy(value) for key, value in source.items()}


def _site_key(site):
    """找到与站点匹配的配置项，优先最长后缀。"""
    domain = normalize_site(site)
    if not domain:
        return None
    matches = [
        key for key in SITE_CONFIGS
        if domain == key or domain.endswith("." + key)
    ]
    if not matches:
        return None
    return max(matches, key=len)


def _apply(config, patch):
    """合并一层覆盖：列表扩展、None 清空、其余直接替换。"""
    for key, value in patch.items():
        if value is None:
            if key in config:
                config[key] = [] if isinstance(config[key], list) else None
            continue
        if isinstance(value, list) and isinstance(config.get(key), list):
            merged = list(config[key])
            for item in value:
                if item not in merged:
                    merged.append(item)
            config[key] = merged
        else:
            config[key] = copy.deepcopy(value)


def get_config(site=None, overrides=None):
    """返回合并后的规则：默认值 → 站点覆盖 → 调用方覆盖。

    列表项（hints/tags）是扩展而不是替换，改版时只补新增的噪声词即可；
    需要从零开始写某个列表时传 None，再单独设置。
    """
    if isinstance(site, dict) and overrides is None:
        overrides, site = site, None
    config = _copy_config(DEFAULT_CONFIG)
    key = _site_key(site)
    if key:
        _apply(config, SITE_CONFIGS[key])
    if overrides:
        _apply(config, overrides)
    return config


def set_site_config(site, **overrides):
    """为站点注册/更新规则，返回该站点已设置的覆盖项（增量）。

    值以列表形式给出时是追加，例如本次改版多了一个“热门”噪声容器。
    """
    domain = normalize_site(site)
    if not domain:
        raise ValueError("site 不能为空")
    current = _copy_config(SITE_CONFIGS.get(domain, {}))
    for key, value in overrides.items():
        if isinstance(value, list) and isinstance(current.get(key), list):
            merged = list(current[key])
            for item in value:
                if item not in merged:
                    merged.append(item)
            current[key] = merged
        else:
            current[key] = copy.deepcopy(value)
    SITE_CONFIGS[domain] = current
    return copy.deepcopy(current)


def remove_site_config(site):
    domain = normalize_site(site)
    SITE_CONFIGS.pop(domain, None)


def reset_site_configs():
    """清空站点覆盖（主要给测试和进程内热更新使用）。"""
    SITE_CONFIGS.clear()


def load_site_config(path):
    """从 JSON 文件加载站点规则。

    支持 {"sites": {"example.com": {...}}} 或直接 {"example.com": {...}}。
    """
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    sites = raw.get("sites", raw) if isinstance(raw, dict) else {}
    for domain, overrides in sites.items():
        if isinstance(overrides, dict):
            set_site_config(domain, **overrides)
    return copy.deepcopy(SITE_CONFIGS)


def save_site_config(path):
    """把当前站点覆盖写回 JSON，便于版本化。"""
    payload = {"sites": copy.deepcopy(SITE_CONFIGS)}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    return payload


def attr_tokens(node):
    """把 class/id/role/itemprop 拆成小写词，兼容 camelCase 和连字符。"""
    attrs = getattr(node, "attrs", {}) or {}
    values = [
        attrs.get("class", ""), attrs.get("id", ""), attrs.get("role", ""),
        attrs.get("itemprop", ""), attrs.get("aria-label", ""),
    ]
    joined = " ".join(values)
    joined = _CAMEL.sub(" ", joined)
    return {part.lower() for part in re.split(r"[^0-9A-Za-z一-鿿]+", joined) if part}


def hint_match(node, hints):
    """返回命中的提示词，没有则 None。中文提示允许子串匹配。

    提示词按最长的优先，避免 "nav" 抢先命中 "navbar" 时丢掉更具体的信息。
    """
    tokens = attr_tokens(node)
    raw = " ".join((getattr(node, "attrs", {}) or {}).values())
    hits = []
    for hint in hints:
        hint = hint.lower()
        if _HINT_CJK.search(hint):
            if hint in raw:
                hits.append(hint)
        elif hint in tokens:
            hits.append(hint)
    return max(hits, key=len) if hits else None
