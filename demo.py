from jobpriority import next_job, submit

submit({"id": 1}); submit({"id": 2})
print(next_job())

# TODO: 第二段演示（实现后补）
