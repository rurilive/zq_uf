"""页面规范化，当前只去首尾空白。"""


def canonical(html):
    return (html or "").strip()


def same(a, b):
    return canonical(a) == canonical(b)
