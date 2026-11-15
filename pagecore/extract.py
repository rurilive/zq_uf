"""采集页面正文抽取：按内容密度和链接比例给块打分，区分正文与噪声。

对外接口：
    extract(html)  正文、保留/剔除的块、把握程度
    blocks(html)   所有块（含被剔除的噪声块）及其分数和理由

页面改版时只调整 pagecore.config 里的站点规则，不改这里的算法。
"""
from .config import get_config, hint_match, normalize_site
from .dom import (ALWAYS_SKIP, LIST_TAGS, UNIT_TAGS, clean_text,
                  is_inline_only, locator, parse, text_of)

# 每个标签大约占 4 个字符的版面，用来计算内容密度
_TAG_COST = 4.0
_TEXT_SEPARATOR = "\n\n"
# article 内部的 header/footer/aside 属于文章自身结构（标题区、文末信息），
# 不能按站点框架剔除；main 等更外层的容器不享受这个例外。
_IN_ARTICLE_OK = {"header", "footer", "aside"}


def _collect(root, config):
    """按文档顺序遍历，返回候选块列表。

    噪声子树整块标出且不再向下展开；用显式栈遍历，
    避免改版后的畸形 HTML 嵌套过深时递归爆栈。
    """
    strip_tags = set(config["strip_tags"]) | ALWAYS_SKIP
    noise_tags = set(config["noise_tags"])
    noise_hints = list(config["noise_hints"])
    content_tags = set(config["content_tags"])
    content_hints = list(config["content_hints"])
    article_tags = set(config["article_tags"])
    raws = []

    def emit(raw):
        if raw["kind"] == "noise" or raw["text"]:
            raws.append(raw)

    def build(child, text, kind, reason, in_content, self_content):
        return {
            "node": child,
            "text": clean_text(text),
            "tag": child.tag,
            "kind": kind,
            "reason": reason,
            "in_content": in_content or self_content,
            "in_list": child.tag in LIST_TAGS,
            "link_chars": _link_chars(child, strip_tags),
            "elements": _element_count(child),
        }

    # 栈帧：[容器节点, 是否在正文容器内, 是否在 article 内, 子节点迭代器]
    stack = [[root, False, False, iter(root.elements())]]
    while stack:
        frame = stack[-1]
        node, in_content, article, children = frame
        try:
            child = next(children)
        except StopIteration:
            stack.pop()
            continue

        if child.tag in strip_tags:
            continue

        self_content = bool(
            child.tag in content_tags or hint_match(child, content_hints)
        )
        is_noise_tag = child.tag in noise_tags and not (
            article and child.tag in _IN_ARTICLE_OK
        )
        hint = hint_match(child, noise_hints)

        if is_noise_tag:
            reason = "noise:tag=%s" % child.tag
            if hint:
                reason += ";hint=%s" % hint
            emit(build(child, text_of(child, strip_tags), "noise", reason,
                       in_content, self_content))
            continue
        if hint:
            emit(build(child, text_of(child, strip_tags), "noise",
                       "noise:hint=%s" % hint, in_content, self_content))
            continue
        if child.tag in UNIT_TAGS or is_inline_only(child):
            emit(build(child, text_of(child, strip_tags), "content", "",
                       in_content, self_content))
            continue

        # 容器：自身的直接文本单独成块，子元素继续向下遍历。
        # 直接文本本身不含链接、不占标签版面，避免长正文容器被误判。
        direct = clean_text("".join(
            part for part in child.children if isinstance(part, str)
        ))
        if direct:
            emit({
                "node": child, "text": direct, "tag": child.tag,
                "kind": "content", "reason": "",
                "in_content": in_content or self_content,
                "in_list": False, "link_chars": 0, "elements": 0,
            })
        stack.append([
            child, in_content or self_content,
            article or child.tag in article_tags, iter(child.elements()),
        ])
    return raws


def _link_chars(node, strip_tags):
    """节点内链接文字的字数（含链接里的嵌套标签）。"""
    if node.tag == "a":
        return len(text_of(node, strip_tags))
    return sum(_link_chars(child, strip_tags) for child in node.elements())


def _element_count(node):
    """节点内元素个数，用于估算标签版面开销。"""
    total = 0
    stack = list(node.elements())
    while stack:
        child = stack.pop()
        total += 1
        stack.extend(child.elements())
    return total


def _score(raw, config):
    chars = len(raw["text"])
    if not chars:
        return {"score": 0.0, "density": 0.0, "link_ratio": 0.0}

    link_chars = raw.get("link_chars")
    if link_chars is None:
        link_chars = _link_chars(raw["node"], set(config["strip_tags"]) | ALWAYS_SKIP)
    elements = raw.get("elements")
    if elements is None:
        elements = _element_count(raw["node"])
    link_ratio = min(1.0, link_chars / float(chars))
    density = chars / (chars + _TAG_COST * elements)
    score = density * (1.0 - link_ratio)
    if raw["in_content"]:
        score += float(config["content_boost"])
    return {
        "score": round(min(1.0, max(0.0, score)), 4),
        "density": round(density, 4),
        "link_ratio": round(link_ratio, 4),
    }


def _analyse(html, config):
    raws = _collect(parse(html), config)
    result = []
    for raw in raws:
        metrics = _score(raw, config)
        result.append({
            "index": len(result),
            "tag": raw["tag"],
            "locator": locator(raw["node"]),
            "text": raw["text"],
            "chars": len(raw["text"]),
            "kind": raw["kind"],
            "reason": raw["reason"],
            "in_content": raw["in_content"],
            "in_list": raw.get("in_list", False),
            "keep": False,
            "score": metrics["score"],
            "density": metrics["density"],
            "link_ratio": metrics["link_ratio"],
        })
    return result


def _select(blocks, config):
    """三轮选择。

    1. 阈值内取正文（分数、链接比例、最短长度）；
    2. 正文总量过短时放宽阈值，保住两三段的短文；
    3. 一个块都没有时再保底，保证不空手而归。
    """
    keep_score = float(config["keep_score"])
    min_chars = int(config["min_block_chars"])
    short_chars = int(config["short_body_chars"])
    short_min = int(config["short_min_chars"])
    short_blocks = int(config["short_max_blocks"])
    max_link = float(config["max_link_ratio"])

    candidates = [b for b in blocks if b["kind"] == "content"]
    short_mode = False
    fallback = False

    kept = [
        b for b in candidates
        if b["score"] >= keep_score
        and b["link_ratio"] < max_link
        and (b["chars"] >= min_chars or b["in_content"])
    ]
    if not kept:
        fallback = True
        kept = [
            b for b in candidates if b["chars"] >= 1 and b["link_ratio"] < max_link
        ]
        if not kept:
            # 整页只有链接（列表页/导航页），没有可抽的正文
            fallback = False
    elif (sum(b["chars"] for b in kept) < short_chars
          and len(kept) <= short_blocks):
        # 正文确实很短（只有两三段），放宽阈值把短文保住，把握程度降低。
        # 块数不少只是每段偏短，属于正常文章，不进入短正文模式。
        short_mode = True
        relaxed = [
            b for b in candidates
            if b["chars"] >= short_min and b["link_ratio"] < max_link
        ]
        if relaxed:
            kept = relaxed

    kept_indexes = {b["index"] for b in kept}
    for block in blocks:
        block["keep"] = block["index"] in kept_indexes
        if block["kind"] == "content" and not block["keep"]:
            if block["link_ratio"] >= max_link:
                block["reason"] = "link-ratio=%.2f" % block["link_ratio"]
            elif short_mode:
                block["reason"] = "too-short"
            else:
                block["reason"] = "low-score=%.2f" % block["score"]
    return kept, short_mode, fallback


def _page_type(kept, blocks, short_chars):
    """article / short / list / empty；列表页即使有文字也明显降低把握程度。"""
    content = [b for b in blocks if b["kind"] == "content" and b["chars"]]
    if not content:
        return "empty"

    content_chars = sum(b["chars"] for b in content) or 1
    link_chars = sum(b["chars"] * b["link_ratio"] for b in content)
    if link_chars / float(content_chars) >= 0.5:
        return "list"
    # 正文块大多来自 ul/ol 且不在正文容器内：列表页/索引页，不是文章。
    # 文章正文里的要点列表带有 in_content 标记，不受影响。
    in_list = [b for b in content if b.get("in_list") and not b["in_content"]]
    if len(in_list) >= max(3, len(content) * 0.6):
        return "list"

    kept_chars = sum(b["chars"] for b in kept)
    if not kept_chars:
        return "empty"
    if kept_chars >= short_chars:
        return "article"
    return "short"


def _confidence(kept, blocks, short_mode, fallback, page_type):
    """整体把握程度：块分数、覆盖率为主，短文/回退/列表页分别封顶。"""
    if not kept:
        return 0.0
    total_chars = sum(b["chars"] for b in kept) or 1
    mean_score = sum(b["score"] * b["chars"] for b in kept) / float(total_chars)
    content_chars = sum(b["chars"] for b in blocks if b["kind"] == "content") or 1
    coverage = min(1.0, total_chars / float(content_chars))
    confidence = 0.35 + 0.45 * mean_score + 0.20 * coverage
    if page_type == "list":
        return round(min(confidence, 0.35), 3)
    if fallback:
        confidence = min(confidence, 0.30)
    # 短正文即便块块都像正文，整体把握程度也不能给高
    if short_mode or page_type == "short":
        confidence = min(confidence, 0.45)
    return round(max(0.0, min(1.0, confidence)), 3)


def _public(block):
    return {
        "index": block["index"],
        "tag": block["tag"],
        "locator": block["locator"],
        "text": block["text"],
        "chars": block["chars"],
        "kind": block["kind"],
        "score": block["score"],
        "link_ratio": block["link_ratio"],
        "in_content": block["in_content"],
        "keep": block["keep"],
        "reason": block["reason"],
    }


def blocks(html, site=None, overrides=None):
    """返回页面所有块，噪声块标为 kind=noise、keep=False。

    site 用于匹配 pagecore.config.SITE_CONFIGS 的站点规则，
    overrides 直接覆盖本次调用的规则（改版时按站点微调）。
    """
    config = get_config(site, overrides)
    analysed = _analyse(html, config)
    _select(analysed, config)
    return [_public(b) for b in analysed]


def extract(html, site=None, overrides=None):
    """抽取正文。

    返回字段：
        text        正文，段落之间以空行分隔
        confidence  整体把握程度 0~1；短文、列表页、回退时自动降低
        kept        被当作正文的块（index/locator/score，可直接定位）
        noise       被剔除的噪声块（含命中的规则）
        dropped     被剔除的普通块（低分、链接过多、过短）
        blocks      正文块数
        short       是否进入短正文模式
        fallback    是否退到最低阈值保底
        page_type   article / short / list / empty
        site        归一化后的站点
    """
    config = get_config(site, overrides)
    analysed = _analyse(html, config)
    kept, short_mode, fallback = _select(analysed, config)
    page_type = _page_type(kept, analysed, int(config["short_body_chars"]))
    confidence = _confidence(kept, analysed, short_mode, fallback, page_type)

    kept = sorted(kept, key=lambda b: b["index"])
    return {
        "text": _TEXT_SEPARATOR.join(b["text"] for b in kept if b["text"]),
        "blocks": len(kept),
        "kept": [_public(b) for b in kept],
        "noise": [_public(b) for b in analysed if b["kind"] == "noise"],
        "dropped": [
            _public(b) for b in analysed
            if b["kind"] == "content" and not b["keep"]
        ],
        "confidence": confidence,
        "short": short_mode,
        "fallback": fallback,
        "page_type": page_type,
        "site": normalize_site(site),
    }
