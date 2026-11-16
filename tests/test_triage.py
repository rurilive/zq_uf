import json
import os
import tempfile
import unittest

from crashdedupe import (
    close_issue,
    crash_fingerprint,
    get_issue,
    list_issues,
    merge_issues,
    normalize_insn,
    normalize_stack,
    report,
    reset_store,
    split_issue,
    triage,
    use_store,
)


# 同一空指针解引用原因，输入不同导致栈偏移、寄存器里的地址值不同。
SAME_CAUSE_A = {
    "id": "s1",
    "target": "pdf",
    "stack": ["parse_obj+0x12", "read_dict+0x88", "main+0x40"],
    "signal": "SIGSEGV",
    "fault_addr": "0x0",
    "insn": "mov 0x10(%rax),%rdx",
    "regs": {"rax": "0x0", "rdx": "0x7f2000000008"},
}
SAME_CAUSE_B = {
    "id": "s2",
    "target": "pdf",
    "stack": ["parse_obj+0x31", "read_dict+0x88", "main+0x40"],
    "signal": "SIGSEGV",
    "fault_addr": "0x10",
    "insn": "mov 0x10(%rax),%rdx",
    "regs": {"rax": "0x0", "rdx": "0x7f3111111118"},
}
# 栈几乎一样，但出错指令/访问点不同：另一条独立问题，不许被并掉。
DIFFERENT_INSN = {
    "id": "s3",
    "target": "pdf",
    "stack": ["parse_obj+0x40", "read_dict+0x88", "main+0x40"],
    "signal": "SIGSEGV",
    "fault_addr": "0x8",
    "insn": "mov (%rbx),%rcx",
    "regs": {"rbx": "0x0", "rcx": "0x7f4000000010"},
}
# 信号不同，即使栈顶同名也必须分开。
DIFFERENT_SIGNAL = {
    "id": "s4",
    "target": "pdf",
    "stack": ["parse_obj+0x12", "read_dict+0x88", "main+0x40"],
    "signal": "SIGABRT",
    "fault_addr": "0x0",
    "abort_message": "assertion failed: obj != NULL at parse_obj",
}
# 另一个目标里的独立空指针问题。
OTHER_TARGET = {
    "id": "s5",
    "target": "png",
    "stack": ["decode_chunk+0x20", "main+0x55"],
    "signal": "SIGSEGV",
    "fault_addr": "0x0",
    "insn": "movzbl (%r8),%eax",
    "regs": {"r8": "0x0"},
}


class NormalizeTest(unittest.TestCase):
    def test_stack_drops_offsets_and_folds_addresses(self):
        fp = crash_fingerprint(
            {"stack": ["parse_obj+0x12", "0x4005a2", "??", "main+0x40"]}
        )
        self.assertEqual(fp["stack"], ["parse_obj", "<addr>", "main"])

    def test_insn_normalizes_immediates_but_keeps_registers(self):
        a = normalize_insn("0x4012a0: mov 0x10(%rax),%rdx")
        b = normalize_insn("0x4012a0: mov 0x30(%rax),%rdx")
        self.assertEqual(a["mnemonic"], "mov")
        self.assertEqual(a["access"], "read")
        # 寻址基址寄存器必须被识别出来（它就是故障寄存器）。
        self.assertIn("rax", a["mem_regs"])
        self.assertIn("rdx", a["regs"])
        # 不同立即数偏移归一到同一语义类别（小偏移）后相等。
        self.assertEqual(a["tokens"], b["tokens"])

    def test_insn_parser_handles_intel_arm_and_indexed_addressing(self):
        intel = normalize_insn("mov rdx,[rax+0x10]")
        arm = normalize_insn("ldr x0,[x1,#0x20]")
        indexed = normalize_insn("mov 0x8(%rbx,%rcx,4),%rax")
        self.assertEqual(intel["mem_regs"], ("rax",))
        self.assertTrue(any(tok.startswith("mem[disp:") for tok in intel["tokens"]))
        self.assertEqual(arm["mem_regs"], ("x1",))
        self.assertEqual(indexed["mem_regs"], ("rbx", "rcx"))
        self.assertEqual(indexed["access"], "read")

    def test_minimal_crash_does_not_break_pipeline(self):
        reset_store()
        result = triage([{"id": "empty1"}, {"id": "empty2"}])
        # 两条信息全缺的崩溃走 unknown 桶，按空栈相似度归为一条。
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(result["issues"][0]["samples"], ["empty1", "empty2"])

    def test_register_values_are_classified_not_compared_raw(self):
        fp = crash_fingerprint(SAME_CAUSE_B)
        # 只保留出错指令内存操作数里的寻址寄存器，数据目的 rdx 不参与。
        self.assertEqual(fp["regs"], {"rax": "zero"})
        # 0x10 是 NULL+字段偏移，与 0x0 同属空指针解引用类。
        self.assertEqual(fp["fault_class"], "null_deref")


class TriageTest(unittest.TestCase):
    def setUp(self):
        reset_store()

    def test_same_cause_different_inputs_merge_into_one_issue(self):
        result = triage([SAME_CAUSE_A, SAME_CAUSE_B])
        self.assertEqual(result["new_issues"], 1)
        issue = result["issues"][0]
        self.assertEqual(issue["count"], 2)
        self.assertEqual(issue["samples"], ["s1", "s2"])
        self.assertEqual(issue["representative"], "s1")
        basis = issue["match_basis"]
        self.assertIn("normalized_stack", basis["dimensions"])
        self.assertIn("faulting_insn", basis["dimensions"])
        self.assertIn("register_classes", basis["dimensions"])

    def test_similar_stack_but_different_faulting_insn_stays_separate(self):
        result = triage([SAME_CAUSE_A, SAME_CAUSE_B, DIFFERENT_INSN])
        self.assertEqual(len(result["issues"]), 2)
        ids = {issue["representative"] for issue in result["issues"]}
        self.assertEqual(ids, {"s1", "s3"})

    def test_different_signal_stays_separate(self):
        result = triage([SAME_CAUSE_A, DIFFERENT_SIGNAL])
        self.assertEqual(len(result["issues"]), 2)
        self.assertEqual(result["issues"][0]["signals"], ["SIGSEGV"])

    def test_targets_are_isolated(self):
        result = triage([SAME_CAUSE_A, OTHER_TARGET])
        self.assertEqual(len(result["issues"]), 2)

    def test_all_sample_ids_kept_and_representative_picked(self):
        rich = dict(SAME_CAUSE_A, id="s1b")
        sparse = {
            "id": "s2b",
            "target": "pdf",
            "stack": ["parse_obj+0x99", "read_dict+0x88", "main+0x40"],
            "signal": "SIGSEGV",
            "insn": "mov 0x10(%rax),%rdx",
            "regs": {"rax": "0x0"},
        }
        result = triage([sparse, rich])
        issue = result["issues"][0]
        self.assertEqual(sorted(issue["samples"]), ["s1b", "s2b"])
        # 信息更完整（有故障地址、更深栈、更多寄存器）的样本当代表。
        self.assertEqual(issue["representative"], "s1b")

    def test_duplicate_sample_id_is_counted_once(self):
        triage([SAME_CAUSE_A])
        result = triage([SAME_CAUSE_A, SAME_CAUSE_B])
        issue = get_issue(result["issues"][0]["issue_id"])
        self.assertEqual(issue["samples"], ["s1", "s2"])
        self.assertEqual(issue["count"], 2)

    def test_report_reflects_real_issue_count_per_target(self):
        triage([SAME_CAUSE_A, SAME_CAUSE_B, DIFFERENT_INSN, OTHER_TARGET])
        body = report()
        # 4 份样本只对应 3 条真实问题，而不是按文件数报 4 个。
        self.assertEqual(body["issues"], 3)
        self.assertEqual(body["open_issues"], 3)
        self.assertEqual(body["by_target"]["pdf"]["samples"], 3)
        self.assertEqual(body["by_target"]["pdf"]["open"], 2)
        self.assertEqual(body["by_target"]["png"]["samples"], 1)
        self.assertEqual(len(body["representatives"]), 3)

    def test_closed_issue_reopens_with_fix_version_recorded(self):
        result = triage([SAME_CAUSE_A])
        issue_id = result["issues"][0]["issue_id"]
        close_issue(issue_id, "v1.2.0")
        self.assertEqual(get_issue(issue_id)["status"], "closed")
        self.assertEqual(report()["open_issues"], 0)

        again = triage([SAME_CAUSE_B], version="v1.3.0")
        reopened = again["issues"][0]
        self.assertEqual(reopened["issue_id"], issue_id)
        self.assertEqual(reopened["status"], "regressed")
        self.assertEqual(reopened["fixed_version"], "v1.2.0")
        # regression_versions 记录重新出现的构建版本，fixed_version 保留修复点。
        self.assertEqual(reopened["regression_versions"], ["v1.3.0"])
        self.assertEqual(reopened["samples"], ["s1", "s2"])
        history_actions = [entry["action"] for entry in reopened["history"]]
        self.assertIn("reopened", history_actions)

    def test_closed_issue_does_not_accept_unrelated_sample(self):
        result = triage([SAME_CAUSE_A])
        issue_id = result["issues"][0]["issue_id"]
        close_issue(issue_id, "v1.2.0")
        # 不同原因的样本不会塞进已关闭条目，而是新开一条。
        again = triage([DIFFERENT_INSN], version="v1.3.0")
        self.assertEqual(len(again["issues"]), 1)
        self.assertNotEqual(again["issues"][0]["issue_id"], issue_id)
        self.assertEqual(get_issue(issue_id)["samples"], ["s1"])

    def test_manual_merge_then_split_roundtrip(self):
        result = triage([SAME_CAUSE_A, DIFFERENT_INSN])
        ids = [issue["issue_id"] for issue in result["issues"]]
        merged = merge_issues(ids, reason="人工确认同一根因")
        self.assertEqual(merged["count"], 2)
        self.assertEqual(sorted(merged["samples"]), ["s1", "s3"])
        self.assertEqual(merged["match_basis"]["rule"], "manual_merge")

        split = split_issue(
            merged["issue_id"], [["s1"], ["s3"]], reason="重新区分访问点"
        )
        self.assertEqual(len(split), 2)
        self.assertEqual(split[0]["match_basis"]["rule"], "manual_split")
        self.assertEqual(
            sorted(sid for issue in split for sid in issue["samples"]),
            ["s1", "s3"],
        )

    def test_split_requires_full_partition(self):
        result = triage([SAME_CAUSE_A, SAME_CAUSE_B])
        issue_id = result["issues"][0]["issue_id"]
        # 漏样本、重复覆盖都不是合法划分，必须拒绝且不改动原条目。
        with self.assertRaises(ValueError):
            split_issue(issue_id, [["s1"]])
        with self.assertRaises(ValueError):
            split_issue(issue_id, [["s1", "s2"], ["s2"]])
        self.assertEqual(get_issue(issue_id)["samples"], ["s1", "s2"])

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "issues.json")
            use_store(path)
            triage([SAME_CAUSE_A])
            issue_id = list_issues()[0]["issue_id"]
            close_issue(issue_id, "v9.9.9")
            self.assertTrue(os.path.exists(path))

            # 模拟第二天重跑：换一个进程视角重新打开同一个 JSON，
            # 用一份新的同因样本触发回归。
            use_store(path)
            issue = get_issue(issue_id)
            self.assertEqual(issue["status"], "closed")
            self.assertEqual(issue["fixed_version"], "v9.9.9")
            again = triage([SAME_CAUSE_B], version="v10.0.0")
            self.assertEqual(again["issues"][0]["issue_id"], issue_id)
            self.assertEqual(again["issues"][0]["status"], "regressed")
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
            self.assertIn("s2", payload["issues"][0]["samples"])


if __name__ == "__main__":
    unittest.main()
