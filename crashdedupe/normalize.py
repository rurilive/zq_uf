"""崩溃特征归一化。

归并的目标是“同一崩溃原因”而不是“同一栈顶函数”，因此在比较前先把
调用栈、出错指令、寄存器状态和故障地址都归一化成与具体输入无关的特征：

- 栈帧去掉偏移量（``parse_obj+0x12`` -> ``parse_obj``），地址帧折叠；
- 出错指令去掉具体地址和立即数，只留助记符、寄存器和访问类型；
- 寄存器/故障地址按语义分类（空指针、堆、栈、野指针……），
  具体数值不参与比较，避免同因不同输入被拆成多条。
"""

import re
from difflib import SequenceMatcher

# 参与比较的栈深度：模糊测试里同一原因的浅层调用路径通常稳定，
# 更深的帧受输入影响大，截断后噪声更小。
STACK_DEPTH = 8

_HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$")
_OFFSET_RE = re.compile(r"\+0x[0-9a-fA-F]+$")
_INSN_ADDR_RE = re.compile(r"^\s*(0x[0-9a-fA-F]+|=[>]?)\s*:?\s*")
_ANY_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
_DEC_RE = re.compile(r"^\d+$")
# 通用寄存器写法：AT&T 的 %rax、Intel 的 rax、r10d、eax、xmm0 等。
_REG_TOKEN_RE = re.compile(r"[%$]?[a-z][a-z0-9]{1,4}\b")
_NON_REGISTER = {"byte", "word", "dword", "qword", "ptr", "xword", "tbyte"}

# 助记符 -> 访问类型。指令归一化只看助记符，不依赖具体架构表。
_ACCESS_HINTS = (
    (("mov", "movs", "movzx", "movsx", "movabs", "load", "ldr", "lb", "lw",
      "cmp", "test", "lea"), "read"),
    (("str", "stp", "stb", "stw", "store", "mov.write"), "write"),
    (("call", "callq", "bl", "blr"), "call"),
    (("jmp", "jmpq", "br", "ret", "retq", "je", "jne", "jz", "jnz"), "control"),
)


def parse_int(value):
    """把 int 或 0x 前缀的字符串解析成 int，无法解析返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            if text.lower().startswith("0x"):
                return int(text, 16)
            return int(text)
        except ValueError:
            return None
    return None


def classify_value(value):
    """把一个具体数值归一成语义类别，而不是按原值比较。

    同一原因在不同输入下寄存器里的具体地址几乎必然不同，但地址落在
    哪个区域（空指针、堆、栈、野指针）是稳定的。
    """
    num = parse_int(value)
    if num is None:
        return "unknown"
    if num == 0:
        return "zero"
    if num < 0x10000:
        return "small"
    # 常见用户态堆/动态库映射范围。
    if 0x10000000 <= num < 0x800000000000:
        return "heap_or_map"
    # 高规范地址，典型的线程栈区域。
    if num >= 0x7F0000000000:
        return "stack"
    # 低地址非零：空指针附近的字段偏移解引用。
    if num < 0x10000000:
        return "null_area"
    return "wild"


def classify_fault_addr(value):
    """故障地址分类：0 与低地址字段偏移合并成同一类空指针解引用。

    同一空指针 bug 在不同输入下会以 ``mov 0x10(%rax)`` 之类的形式
    报成 fault_addr=0x10，和 fault_addr=0 是同一原因，必须同桶。
    """
    num = parse_int(value)
    if num is None:
        return "unknown"
    if num < 0x10000:
        return "null_deref"
    if 0x10000 <= num < 0x10000000:
        return "null_area"
    if 0x10000000 <= num < 0x7F0000000000:
        return "heap_or_map"
    return "stack_or_wild"


def normalize_frame(frame):
    """归一化单个栈帧：去偏移、折叠纯地址帧。

    ``parse_obj+0x12`` -> ``parse_obj``；``0x4005a2`` -> ``<addr>``；
    无法识别的帧返回 None，由调用方跳过。
    """
    if frame is None:
        return None
    text = str(frame).strip()
    if not text or text in ("??", "?", "0x0"):
        return None
    # 整帧就是一个地址（未符号化的返回地址），折叠成统一标记。
    if _HEX_RE.match(text):
        return "<addr>"
    text = _OFFSET_RE.sub("", text)
    text = text.split("@plt", 1)[0].strip()
    return text or None


def normalize_stack(crash):
    """把调用栈归一化为函数名序列，截断到 STACK_DEPTH。"""
    frames = []
    for raw in crash.get("stack") or []:
        name = normalize_frame(raw)
        if name is not None:
            frames.append(name)
        if len(frames) >= STACK_DEPTH:
            break
    return frames


def stack_similarity(left, right):
    """两个归一化栈的相似度，0~1；空栈之间定义为 1。"""
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return SequenceMatcher(a=left, b=right).ratio()


def _access_of(mnemonic):
    for names, access in _ACCESS_HINTS:
        if mnemonic in names:
            return access
    return "other"


def _split_operands(text):
    """按逗号切分操作数，但括号内部的逗号（AT&T 索引寻址）保持完整。"""
    operands = []
    depth = 0
    current = []
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            operands.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        operands.append("".join(current).strip())
    return [op for op in operands if op]


def normalize_insn(insn):
    """归一化出错指令。

    支持字符串（``mov %rax,(%rbx)``）或字典（``{"text": ..., "access": ...}``）。
    去掉指令地址，把立即数替换成语义类别，寄存器小写化，
    保留助记符、寄存器和访问类型——同一指令在不同输入下立即数不同，
    归一后仍然相等。
    """
    if isinstance(insn, dict):
        text = insn.get("text") or insn.get("mnemonic") or ""
        explicit_access = insn.get("access")
    else:
        text = insn
        explicit_access = None
    if not isinstance(text, str) or not text.strip():
        return None

    text = _INSN_ADDR_RE.sub("", text.strip())
    parts = text.split(None, 1)
    mnemonic = parts[0].lower()
    operand_text = parts[1] if len(parts) > 1 else ""

    tokens = []
    regs = []
    mem_regs = []

    def add_register(name, in_memory=False):
        name = name.lstrip("%$").lower()
        regs.append(name)
        if in_memory:
            mem_regs.append(name)
        tokens.append("reg:" + name)

    # 逐个操作数解析，内存引用 disp(%base,%index,scale) 整体保留为一个 token，
    # 这样同一条加载指令在不同输入下归一结果稳定。
    for op in _split_operands(operand_text):
        op = op.strip()
        if not op:
            continue
        hex_match = _ANY_HEX_RE.fullmatch(op)
        if hex_match:
            tokens.append("imm:" + classify_value(op))
            continue
        if _DEC_RE.fullmatch(op):
            num = int(op)
            tokens.append("imm:" + ("small" if num < 0x100 else classify_value(num)))
            continue
        if "(" in op or "[" in op:
            mem_parts = []
            # 位移立即数在 AT&T 里是前缀（0x8(...)），在 Intel/ARM 里在
            # 括号内（[rax+0x10] / [x1,#0x20]），统一扫描全部 hex。
            disps = _ANY_HEX_RE.findall(op)
            if disps:
                mem_parts.append("disp:" + classify_value(disps[0]))
            # 去掉位移立即数后再扫寄存器，避免 0x10 里的 x10 被误认成寄存器。
            remainder = _ANY_HEX_RE.sub(" ", op)
            for match in _REG_TOKEN_RE.finditer(remainder):
                name = match.group(0)
                if name in _NON_REGISTER:
                    continue
                add_register(name, in_memory=True)
                mem_parts.append("reg:" + name.lstrip("%$").lower())
            tokens.append("mem[" + "+".join(mem_parts) + "]")
            continue
        match = _REG_TOKEN_RE.fullmatch(op)
        if match and op not in _NON_REGISTER:
            add_register(match.group(0))
            continue
        tokens.append(op.lower())

    return {
        "mnemonic": mnemonic,
        "tokens": tuple(tokens),
        "regs": tuple(regs),
        "mem_regs": tuple(mem_regs),
        "access": explicit_access or _access_of(mnemonic),
    }


def insn_similarity(left, right):
    """两条归一化指令的相似度：助记符必须一致，操作符按序列比对。

    两侧都没有指令信息时返回 None 表示“无法裁决”，由调用方按中立处理，
    不能把信息缺失误判成指令不同。
    """
    if left is None and right is None:
        return None
    if left is None or right is None:
        return 0.0
    if left["mnemonic"] != right["mnemonic"]:
        return 0.0
    score = 0.5
    if left["tokens"] == right["tokens"]:
        score += 0.4
    else:
        ratio = SequenceMatcher(a=left["tokens"], b=right["tokens"]).ratio()
        score += 0.4 * ratio
    if left["access"] == right["access"]:
        score += 0.1
    return score


def normalize_registers(crash, insn_info):
    """把出错指令涉及的寄存器状态归一成 {寄存器: 语义类别}。

    只保留指令操作数里实际出现的寄存器——它们决定了故障点行为；
    其余寄存器（如数据目的、调用者遗留值）随输入变化，纳入比较会把
    同一原因的样本拆开。具体值不保留，只保留分类。
    """
    regs = crash.get("regs") or crash.get("registers") or {}
    if not isinstance(regs, dict) or not insn_info:
        return {}
    # 内存访问指令里只有寻址寄存器决定故障；非内存指令才看全部操作数寄存器。
    names = insn_info["mem_regs"] or insn_info["regs"]
    result = {}
    lowered = {name.lower(): name for name in regs}
    for name in names:
        actual = lowered.get(name) or lowered.get(name.lstrip("%$").lower())
        if actual is not None:
            result[name] = classify_value(regs[actual])
    return result


def normalize_message(message):
    """归一化 abort/assert 消息：抹掉地址和数字，保留文本骨架。"""
    if not message:
        return None
    text = str(message)
    text = _ANY_HEX_RE.sub("<addr>", text)
    text = re.sub(r"\b\d+\b", "<n>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:200] or None


def crash_fingerprint(crash):
    """提取一条崩溃的完整归一化特征。

    指纹用于记录“为什么归并”，不直接要求两条崩溃指纹完全相等——
    同一原因在不同输入下栈会有小差异，匹配在 triage 层按规则做。
    """
    insn_info = normalize_insn(
        crash.get("insn") or crash.get("fault_insn") or crash.get("instruction")
    )
    stack = normalize_stack(crash)
    regs = normalize_registers(crash, insn_info)
    return {
        "target": crash.get("target") or "unknown",
        "signal": crash.get("signal") or "unknown",
        "stack": stack,
        "top_frame": stack[0] if stack else "<empty>",
        "insn": insn_info,
        "access": insn_info["access"] if insn_info else "unknown",
        "regs": regs,
        "fault_class": classify_fault_addr(crash.get("fault_addr")),
        "message": normalize_message(crash.get("abort_message")),
    }


def cause_key(fp):
    """粗分桶键：硬条件不同（信号/故障点/访问类型）必然不是同一原因。

    故障地址缺失时不臆造类别，用通配桶，保证信息不全的样本仍能被
    同因条目纳入候选——最终是否并入仍由相似度门槛裁决。
    """
    return (
        fp["target"],
        fp["signal"],
        fp["top_frame"],
        fp["access"],
        fp["fault_class"],
        fp["message"] or "",
    )


def compatible_cause_keys(key):
    """生成一个键可以互相进入候选集的等价键（unknown 与具体类别互通）。"""
    target, signal, top, access, fault_class, message = key
    if fault_class == "unknown":
        return {
            key,
            (target, signal, top, access, "null_deref", message),
            (target, signal, top, access, "heap_or_map", message),
            (target, signal, top, access, "null_area", message),
            (target, signal, top, access, "stack_or_wild", message),
        }
    return {key, (target, signal, top, access, "unknown", message)}
