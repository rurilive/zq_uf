"""索引一批文章再查重：改标题转载、长短文本、增量索引与持久化。"""

import os
import tempfile

from dupfinder import DupFinder

BODY_METRO = ("近日记者从市交通委获悉，地铁三号线二期工程已完成主体结构封顶，"
              "预计明年六月开始联调联试，年底具备通车条件。线路全长十八公里，"
              "共设车站十二座，建成后将显著缓解东部城区的通勤压力。")
BODY_RIVER = ("生态环境部门通报，跨区河流治理二期项目本月开工，重点整治沿线"
              "排污口四十余处，同步建设生态护岸和湿地缓冲带，计划后年完工。")
BODY_TECH = ("本地一家芯片企业发布新一代车规级处理器，采用五纳米工艺，"
             "算力较上代提升三倍，已获得多家整车厂定点，预计明年量产。")

finder = DupFinder(threshold=0.6)
total = finder.index([
    {"id": "m1", "title": "地铁三号线年底通车", "text": BODY_METRO},
    {"id": "r1", "title": "河流治理二期开工", "text": BODY_RIVER},
    {"id": "t1", "title": "五纳米车规芯片发布", "text": BODY_TECH},
])
print("已索引 %d 篇" % total)

# 1. 转载改了标题，正文不变
print("\n[查重] 改标题转载：")
for hit in finder.explain({"title": "东部通勤利好！新线来了", "text": BODY_METRO}):
    print("  命中 %s 相似度 %.2f" % (hit["id"], hit["similarity"]))
    print("  依据：%s" % hit["reason"])
    print("  共同片段：%s" % " / ".join(hit["common"][:3]))

# 2. 短稿是长稿的摘录（长短文本分别归一）
digest = " ".join(BODY_RIVER.split("，")[:2])
long_version = BODY_RIVER + "据悉，项目还将配套建设智慧水质监测平台。" * 8
finder.index([{"id": "r2-long", "title": "河流治理详解", "text": long_version}])
print("\n[查重] 短稿 vs 长稿：")
for hit in finder.explain({"text": digest}):
    print("  命中 %s 相似度 %.2f（%s）" % (hit["id"], hit["similarity"], hit["metric"]))

# 3. 无关文章不命中
print("\n[查重] 无关文章：", finder.query({"text": "完全无关的体育新闻快讯。"}))

# 4. 增量索引 + 持久化后再查
path = os.path.join(tempfile.gettempdir(), "dupfinder-demo.pkl")
finder.save(path)
loaded = DupFinder.load(path)
loaded.index([{"id": "m2", "title": "三号线二期封顶", "text": BODY_METRO}])
print("\n[持久化] 加载后增量索引，查重：",
      loaded.query({"id": "new", "text": BODY_METRO}))
