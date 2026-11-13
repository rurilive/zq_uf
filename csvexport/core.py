"""按默认方言写 CSV。"""
import csv


def export(rows, path, options=None):
    options = options or {}
    with open(path, "w", newline="", encoding=options.get("encoding", "utf-8")) as handle:
        writer = csv.writer(handle)
        for row in rows:
            writer.writerow(row)
    return path
