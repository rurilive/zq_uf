"""演示：同一原因的多份崩溃归并成一条、相似栈不同原因保持独立、
修复后回归自动重开。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crashdedupe import close_issue, merge_issues, report, reset_store, triage

# 第一轮夜跑：3 份样本，其中 s1/s2 是同一空指针 bug 的不同输入，
# s3 栈几乎一样但出错指令不同，是另一条独立问题。
ROUND_1 = [
    {
        "id": "s1",
        "target": "pdf",
        "stack": ["parse_obj+0x12", "read_dict+0x88", "main+0x40"],
        "signal": "SIGSEGV",
        "fault_addr": "0x0",
        "insn": "mov 0x10(%rax),%rdx",
        "regs": {"rax": "0x0", "rdx": "0x7f2000000008"},
    },
    {
        "id": "s2",
        "target": "pdf",
        "stack": ["parse_obj+0x31", "read_dict+0x88", "main+0x40"],
        "signal": "SIGSEGV",
        "fault_addr": "0x10",
        "insn": "mov 0x10(%rax),%rdx",
        "regs": {"rax": "0x0", "rdx": "0x7f3111111118"},
    },
    {
        "id": "s3",
        "target": "pdf",
        "stack": ["parse_obj+0x40", "read_dict+0x88", "main+0x40"],
        "signal": "SIGSEGV",
        "fault_addr": "0x8",
        "insn": "mov (%rbx),%rcx",
        "regs": {"rbx": "0x0", "rcx": "0x7f4000000010"},
    },
]

reset_store()
result = triage(ROUND_1)
print("第一轮：%d 份样本 -> %d 条真实问题" % (result["total"], len(result["issues"])))
for issue in result["issues"]:
    print(
        "  %s 样本=%s 代表=%s 依据=%s"
        % (
            issue["issue_id"],
            issue["samples"],
            issue["representative"],
            issue["match_basis"]["dimensions"],
        )
    )
print("汇总：", report())

# 人工确认两条其实同源，合并；也可以用 split_issue 再拆开。
merged = merge_issues(
    [issue["issue_id"] for issue in result["issues"]], reason="同一空指针根因"
)
print("人工合并后：", merged["issue_id"], merged["samples"])

# 第一条问题在 v1.2.0 修复并关闭；第二轮夜跑同因样本再次出现 -> 自动重开。
close_issue(merged["issue_id"], fixed_version="v1.2.0")
regression = triage(
    [
        {
            "id": "s4",
            "target": "pdf",
            "stack": ["parse_obj+0x55", "read_dict+0x88", "main+0x40"],
            "signal": "SIGSEGV",
            "fault_addr": "0x0",
            "insn": "mov 0x10(%rax),%rdx",
            "regs": {"rax": "0x0"},
        }
    ],
    version="v1.3.0",
)
reopened = regression["issues"][0]
print(
    "回归重开：status=%s fixed_version=%s 全部样本=%s"
    % (reopened["status"], reopened["fixed_version"], reopened["samples"])
)
print("最终汇总：", report())
