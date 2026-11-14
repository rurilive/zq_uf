"""渲染等待：组合条件轮询，替代固定睡眠。

主接口保持 ``wait(page, condition)`` 不变，另提供可选的预算与时钟参数：

    wait(page, condition, timeout=2.0, interval=0.1, *, clock=None, sleep=None)

条件四类叶子（selector / text / network_idle / script）可按 all（与）、
any（或）任意嵌套组合；旧版可调用条件仍然兼容。纯标准库实现。
"""

import time

DEFAULT_TIMEOUT = 2.0
DEFAULT_INTERVAL = 0.1
MAX_NESTING = 32

LEAF_TYPES = ("selector", "text", "network_idle", "script")


# --------------------------------------------------------------------------
# 条件构造助手（直接写字典也可以，助手只做一层校验）
# --------------------------------------------------------------------------

def selector(css):
    """CSS 选择器出现：page.exists(css) 为真。"""
    if not isinstance(css, str) or not css.strip():
        raise ValueError("selector 条件的 target 必须是非空字符串")
    return {"type": "selector", "target": css}


def text(substr):
    """页面包含文本：page.text_contains(substr) 为真。"""
    if not isinstance(substr, str) or not substr:
        raise ValueError("text 条件的 target 必须是非空字符串")
    return {"type": "text", "target": substr}


def network_idle(idle=True):
    """网络空闲状态：page.network_idle() == idle（默认要求进入空闲）。"""
    if not isinstance(idle, bool):
        raise ValueError("network_idle 条件的 target 必须是布尔值")
    return {"type": "network_idle", "target": idle}


def script(code):
    """脚本完成：page.eval_script(code) 为真。"""
    if not isinstance(code, str) or not code.strip():
        raise ValueError("script 条件的 target 必须是非空字符串")
    return {"type": "script", "target": code}


def all_of(*conditions):
    """与组合：全部叶子命中才算命中。"""
    if not conditions:
        raise ValueError("all 组合至少包含一个条件")
    return {"all": list(conditions)}


def any_of(*conditions):
    """或组合：任意叶子命中即算命中。"""
    if not conditions:
        raise ValueError("any 组合至少包含一个条件")
    return {"any": list(conditions)}


# --------------------------------------------------------------------------
# 条件校验：非法参数在等待开始前一次性报错，不浪费预算
# --------------------------------------------------------------------------

def _validate_condition(condition, depth=0):
    if callable(condition):
        return {"type": "callable", "target": condition}
    if not isinstance(condition, dict):
        raise ValueError(
            "condition 必须是可调用对象或条件字典，实际类型: %s"
            % type(condition).__name__
        )

    keys = set(condition)
    if "all" in keys or "any" in keys:
        if "all" in keys and "any" in keys:
            raise ValueError("组合条件不能同时包含 all 和 any")
        extra = keys - {"all", "any"}
        if extra:
            raise ValueError("组合条件含未知字段: %s" % ", ".join(sorted(extra)))
        op = "all" if "all" in keys else "any"
        children = condition[op]
        if not isinstance(children, list) or not children:
            raise ValueError("%s 的值必须是非空条件列表" % op)
        if depth >= MAX_NESTING:
            raise ValueError("条件嵌套超过 %d 层上限" % MAX_NESTING)
        return {op: [_validate_condition(child, depth + 1) for child in children]}

    ctype = condition.get("type")
    if ctype not in LEAF_TYPES:
        raise ValueError(
            "未知条件类型: %r（支持 %s）" % (ctype, "、".join(LEAF_TYPES))
        )
    extra = keys - {"type", "target", "invert"}
    if extra:
        raise ValueError(
            "条件 %r 含未知字段: %s" % (ctype, ", ".join(sorted(extra)))
        )

    if "target" in condition:
        target = condition["target"]
    elif ctype == "network_idle":
        target = True
    else:
        target = None
    if ctype in ("selector", "text", "script"):
        if not isinstance(target, str) or not target.strip():
            raise ValueError("%s 条件的 target 必须是非空字符串" % ctype)
    elif not isinstance(target, bool):
        raise ValueError("network_idle 条件的 target 必须是布尔值")

    invert = condition.get("invert", False)
    if not isinstance(invert, bool):
        raise ValueError("invert 必须是布尔值")
    return {"type": ctype, "target": target, "invert": invert}


def _public_spec(spec):
    """把内部条件（含 callable 对象）转成可序列化的回显结构。"""
    if "all" in spec or "any" in spec:
        op = "all" if "all" in spec else "any"
        return {op: [_public_spec(child) for child in spec[op]]}
    out = {"type": spec["type"]}
    if spec["type"] == "callable":
        fn = spec["target"]
        out["target"] = getattr(fn, "__name__", repr(fn))
    else:
        out["target"] = spec["target"]
    if spec.get("invert"):
        out["invert"] = True
    return out


# --------------------------------------------------------------------------
# Page 适配：优先调用 page 的协议方法，字典 page 走快照兜底
# --------------------------------------------------------------------------

class _Page:
    def __init__(self, page):
        self._page = page

    def exists(self, css):
        page = self._page
        if hasattr(page, "exists"):
            return bool(page.exists(css))
        if isinstance(page, dict):
            return bool(page.get("selectors", {}).get(css))
        raise TypeError("page 不支持 selector 条件：需要 exists(selector) 方法或字典快照")

    def text_contains(self, substr):
        page = self._page
        if hasattr(page, "text_contains"):
            return bool(page.text_contains(substr))
        if isinstance(page, dict):
            return substr in str(page.get("text", ""))
        raise TypeError("page 不支持 text 条件：需要 text_contains(text) 方法或字典快照")

    def network_idle(self):
        page = self._page
        if hasattr(page, "network_idle"):
            return bool(page.network_idle())
        if isinstance(page, dict):
            return bool(page.get("network_idle", False))
        raise TypeError("page 不支持 network_idle 条件：需要 network_idle() 方法或字典快照")

    def eval_script(self, code):
        page = self._page
        if hasattr(page, "eval_script"):
            return bool(page.eval_script(code))
        if isinstance(page, dict):
            return bool(page.get("scripts", {}).get(code, False))
        raise TypeError("page 不支持 script 条件：需要 eval_script(code) 方法或字典快照")

    def snapshot(self):
        page = self._page
        if hasattr(page, "snapshot"):
            return page.snapshot()
        if isinstance(page, dict):
            return dict(page)
        raise TypeError("page 不支持快照：需要 snapshot() 方法或为字典")


# --------------------------------------------------------------------------
# 条件求值：每轮对全部叶子做一次全量探测，不做短路跳过，
# 保证超时现场里每个叶子都有最后状态。
# --------------------------------------------------------------------------

def _index_leaves(spec, bucket):
    """按深度优先的声明顺序给叶子编号，编号即命中顺序的 tie-break。"""
    if "all" in spec or "any" in spec:
        op = "all" if "all" in spec else "any"
        for child in spec[op]:
            _index_leaves(child, bucket)
    else:
        bucket.append(spec)
    return bucket


def _leaf_id(leaf, index):
    if leaf["type"] == "callable":
        return "callable#%d" % index
    if leaf["type"] == "network_idle":
        return "network_idle#%d" % index
    return "%s:%s#%d" % (leaf["type"], leaf["target"], index)


def _probe(leaf, adapter, raw_page):
    ctype = leaf["type"]
    if ctype == "callable":
        matched = bool(leaf["target"](raw_page))
    elif ctype == "selector":
        matched = adapter.exists(leaf["target"])
    elif ctype == "text":
        matched = adapter.text_contains(leaf["target"])
    elif ctype == "network_idle":
        matched = adapter.network_idle() is leaf["target"]
    else:  # script
        matched = adapter.eval_script(leaf["target"])
    return not matched if leaf.get("invert") else matched


def _evaluate(spec, leaves, adapter, raw_page):
    states = []

    def visit(node):
        if "all" in node or "any" in node:
            op = "all" if "all" in node else "any"
            results = [visit(child) for child in node[op]]
            return all(results) if op == "all" else any(results)
        index = len(states)  # DFS 追加顺序与 _index_leaves 编号一致
        leaf = leaves[index]
        matched = _probe(leaf, adapter, raw_page)
        states.append({
            "id": _leaf_id(leaf, index),
            "type": leaf["type"],
            "matched": matched,
        })
        return matched

    return visit(spec), states


# --------------------------------------------------------------------------
# 快照归一化与差异
# --------------------------------------------------------------------------

def _normalize(value):
    if isinstance(value, dict):
        return {
            str(key): _normalize(val)
            for key, val in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return sorted((_normalize(item) for item in value), key=lambda item: repr(item))
    return value


def _snapshot_diff(before, after):
    """对比首末快照，返回 added / changed / removed 三类差异。"""
    before = _normalize(before)
    after = _normalize(after)
    if not isinstance(before, dict):
        before = {"snapshot": before}
    if not isinstance(after, dict):
        after = {"snapshot": after}

    added, changed, removed = {}, {}, {}
    for key, val in after.items():
        if key not in before:
            added[key] = val
        elif before[key] != val:
            changed[key] = {"before": before[key], "after": val}
    for key, val in before.items():
        if key not in after:
            removed[key] = val

    diff = {}
    if added:
        diff["added"] = added
    if changed:
        diff["changed"] = changed
    if removed:
        diff["removed"] = removed
    return diff


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def wait(page, condition, timeout=DEFAULT_TIMEOUT, interval=DEFAULT_INTERVAL,
         *, clock=None, sleep=None):
    """按组合条件轮询等待页面渲染。

    预算口径：``timeout`` 是总预算（秒），结果里的 ``waited`` 原样回显；
    每轮睡眠 ``min(interval, 剩余预算)``，预算耗尽前保证再探测一次。
    命中立即返回；超时返回最后状态与首末快照差异。

     clock/sleep 仅用于测试注入，生产代码不传，默认走 time 模块。
    """
    spec = _validate_condition(condition)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("timeout 必须是正数秒，实际值: %r" % (timeout,))
    if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval <= 0:
        raise ValueError("interval 必须是正数秒，实际值: %r" % (interval,))
    if interval > timeout:
        raise ValueError("interval(%r) 不能大于 timeout(%r)" % (interval, timeout))

    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    adapter = _Page(page)
    leaves = _index_leaves(spec, [])

    started = clock()
    deadline = started + timeout
    first_snapshot = adapter.snapshot()

    polls = 0
    delays = []
    first_hit = {}  # 叶子 id -> 首次命中的耗时秒
    last_states = []
    matched = False

    while True:
        elapsed = clock() - started
        matched, states = _evaluate(spec, leaves, adapter, page)
        polls += 1
        for state in states:
            if state["matched"] and state["id"] not in first_hit:
                first_hit[state["id"]] = round(elapsed, 6)
        last_states = states

        if matched:
            break
        remaining = deadline - clock()
        if remaining <= 0:
            break
        # 重试间隔按剩余预算收缩：最后一段不会睡过预算
        delay = min(interval, remaining)
        delays.append(round(delay, 6))
        sleep(delay)

    elapsed = round(clock() - started, 6)
    last_snapshot = adapter.snapshot()
    id_order = [_leaf_id(leaf, i) for i, leaf in enumerate(leaves)]

    result = {
        "ok": matched,
        "waited": timeout,
        "elapsed": elapsed,
        "polls": polls,
        "delays": delays,
        "condition": _public_spec(spec),
    }

    if matched:
        # 命中顺序：先按首次命中耗时，再按叶子声明顺序（DFS 编号）
        result["hit"] = sorted(
            (leaf_id for leaf_id in id_order if leaf_id in first_hit),
            key=lambda leaf_id: (first_hit[leaf_id], id_order.index(leaf_id)),
        )
        result["last_snapshot"] = last_snapshot
        return result

    # 超时现场：每个叶子的最后状态、首末快照及其差异、曾命中过的叶子
    result["last_state"] = last_states
    result["first_snapshot"] = first_snapshot
    result["snapshot"] = last_snapshot
    result["diff"] = _snapshot_diff(first_snapshot, last_snapshot)
    partial = [state["id"] for state in last_states if state["matched"]]
    if partial:
        result["hit"] = partial
    return result
