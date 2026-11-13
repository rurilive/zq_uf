from crawlquota import CAPS, acquire, release, usage

acquire("job-1", "thread", 4)
acquire("job-2", "thread", 2)
print("thread 占用", usage()["thread"], "上限", CAPS["thread"])
release("job-1", "thread", 4)
print("释放后", usage()["thread"])

# TODO: 第二段演示（实现后补）
