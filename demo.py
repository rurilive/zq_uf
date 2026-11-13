from glossary import (conflicts, define, fields_of, history, lookup,
                      map_field, notifications, rename, search)

define({"name": "GMV", "definition": "统计周期内已支付订单金额合计",
        "aliases": ["成交额"], "abbreviations": ["GMV"],
        "caliber": "含退款，按支付时间", "owner": "张三",
        "source": "交易域", "valid_from": "2026-01-01"})
define({"name": "实收金额", "definition": "扣除退款后的实际到账金额",
        "caliber": "不含退款", "owner": "李四", "source": "财务域"})

map_field("orders", "pay_amount", "GMV", owner="王五")
map_field("orders", "pay_amount", "实收金额")
map_field("daily_report", "gmv_sum", "GMV", owner="赵六")

define({"name": "GMV", "definition": "不含退款的支付金额合计",
        "source": "财务域"})
rename("实收金额", "净成交额")

print(lookup("GMV"))
print(search("成交额")[0]["snippet"])
print(fields_of("GMV"))
print(history("净成交额")[0]["name"])
print(conflicts()[0]["previous"]["source"])
print(notifications(owner="王五"))
