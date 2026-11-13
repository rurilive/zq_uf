"""把页面文本存成一个文件。"""
import os


def save(page, directory):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, page["id"] + ".txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(page.get("text", ""))
    return path


def diff(a, b):
    text_a = open(a, encoding="utf-8").read()
    text_b = open(b, encoding="utf-8").read()
    return {"length_a": len(text_a), "length_b": len(text_b), "changed": text_a != text_b}
