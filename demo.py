"""演示：分层语言识别 + 人工复核 + 站点汇总 + 接入评估。"""

import json

from langdetect2 import (
    SiteStats, apply_override, confident, detect, evaluate_onboarding,
)

EN = ("The quick brown fox jumps over the lazy dog. This is an English "
      "paragraph with enough words for the system to make a decision today.")
JA = "これは日本語の文章です。システムが言語を正しく识别できるか确认するテストです。"
ZH = "这是一个中文段落，需要通过汉字字符范围来识别语言。系统应该支持多语言页面的正确分流。"

# 1. 基本识别：响应头只是声明之一，正文证据可以推翻它
print("== 单页识别")
page = {"url": "https://news.example.jp/a",
        "headers": {"content-language": "en"},  # 头写错了
        "text": JA}
result = detect(page)
print(json.dumps({k: result[k] for k in
                  ("language", "ratio", "confidence", "confidence_reason",
                   "declared_languages", "declaration_conflict")},
                 ensure_ascii=False, indent=2))

# 2. 多语言混排：主语言 + 各语言占比 + 段落明细 + 原文样本
print("\n== 混排页面")
mixed_page = {"url": "https://site.example.com/mixed",
              "text": EN + "\n\n" + ZH}
mixed = detect(mixed_page)
print("主语言:", mixed["language"], "| 占比:", mixed["ratio"],
      "| 把握:", mixed["confidence"])
for para in mixed["paragraphs"]:
    print("  段落:", para["language"], para["confidence"],
          "| 样本:", para["sample"][:24], "...")

# 3. 入库分流：低把握不直接入库，人工覆盖后保留原识别结果
print("\n== 入库分流与人工覆盖")
view = confident(mixed_page)
print("可直接入库:", view["indexable"], "| 需复核:", view["needs_review"])
reviewed = apply_override(mixed, "zh", reviewer="alice", note="中文为主")
print("覆盖后语言:", reviewed["language"],
      "| 原识别保留:", reviewed["override"]["original_language"],
      reviewed["override"]["original_confidence"])

# 4. 按站点汇总
print("\n== 站点汇总")
stats = SiteStats()
for i in range(18):
    stats.add(detect({"url": f"https://site.example.com/p{i}", "text": EN}))
stats.add(mixed)
stats.add(reviewed)
print(json.dumps(stats.summary("site.example.com"), ensure_ascii=False, indent=2))

# 5. 新语言站点接入：先跑样本，稳定后才开量
print("\n== 接入评估")
samples = [detect({"url": f"https://new.example.com/{i}", "text": EN})
           for i in range(20)]
report = evaluate_onboarding(samples, expected_language="en")
print("可开量:", report["ready"], "| 检查项:", report["checks"])
