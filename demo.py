import glob
import os
import tempfile

from shardwrite import read, write

TOTAL = 100000
BATCH = 5000
DATES = ("2026-03-01", "2026-03-02", "2026-03-03")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "store")
        next_id = 0
        written = 0
        while written < TOTAL:
            batch = []
            for _ in range(min(BATCH, TOTAL - written)):
                day = DATES[next_id % len(DATES)]
                batch.append({
                    "id": next_id,
                    "date": day,
                    "timestamp": "%sT%02d:%02d:%02d" % (
                        day, (next_id // 3600) % 24, (next_id // 60) % 60, next_id % 60
                    ),
                })
                next_id += 1
            written += write(batch, root, max_rows=10000)

        manifests = glob.glob(os.path.join(root, "*", "*.manifest.json"))
        print("写入行数:", written)
        print("分片数量:", len(manifests))
        print("存储目录:", root)
        for date in sorted(os.listdir(root)):
            print(" ", date, "片数:", len(os.listdir(os.path.join(root, date))) // 2)

        all_rows = read(root)
        day_rows = read(root, date="2026-03-02")
        range_rows = read(root, start="2026-03-02", end="2026-03-03")
        print("全部读回:", len(all_rows))
        print("按日期 2026-03-02:", len(day_rows))
        print("范围 2026-03-02~2026-03-03:", len(range_rows))
        assert len(all_rows) == TOTAL
        expected = {day: sum(1 for index in range(TOTAL) if DATES[index % len(DATES)] == day) for day in DATES}
        assert len(day_rows) == expected["2026-03-02"]
        assert len(range_rows) == expected["2026-03-02"] + expected["2026-03-03"]


if __name__ == "__main__":
    main()
