"""按空格切分日志行的最小实现。"""


def parse(lines):
    rows = []
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        rows.append({"time": parts[0], "level": parts[1], "text": " ".join(parts[2:])})
    return rows
