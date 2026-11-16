from samplededupe import compare, failures, family_view, ingest, report, set_threshold

FAMILY = "ransom"
BASE = bytes(range(256)) * 8
ONE_BYTE_OFF = bytearray(BASE)
ONE_BYTE_OFF[100] ^= 0x5A
TWO_BYTES_OFF = bytearray(BASE)
TWO_BYTES_OFF[100] ^= 0x5A
TWO_BYTES_OFF[900] ^= 0x11


def show(title, value):
    print("\n== {} ==".format(title))
    if value is not None:
        print(value)


for sample in (
    {"name": "locker-a.bin", "digest": "aa11", "family": FAMILY,
     "content": BASE, "size": len(BASE)},
    {"name": "locker-a-copy.bin", "digest": "aa11", "family": FAMILY,
     "content": BASE, "size": len(BASE)},
    {"name": "locker-a1.bin", "digest": "aa12", "family": FAMILY,
     "content": bytes(ONE_BYTE_OFF), "size": len(BASE)},
    {"name": "locker-a2.bin", "digest": "aa13", "family": FAMILY,
     "content": bytes(TWO_BYTES_OFF), "size": len(BASE)},
    {"name": "broken.bin", "family": FAMILY},
):
    show(sample["name"], ingest(sample))

show("家族视角（组 + 代表 + 差异）", family_view(FAMILY))
show("逐份对比", compare("locker-a.bin", "locker-a2.bin"))
show("失败留档（按原因筛选）", failures(reason="missing_digest"))
show("按家族调整阈值并重算", set_threshold(FAMILY, 0.995))
show("调整后的家族视角", family_view(FAMILY)["group_count"])
show("报表", report())
