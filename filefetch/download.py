"""附件下载的最小实现。"""


def download(url, dest, open_url):
    data = open_url(url)
    with open(dest, "wb") as handle:
        handle.write(data)
    return len(data)
