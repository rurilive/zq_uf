from crawlpause import is_paused, pause, resume

pause("task-1")
print(is_paused("task-1"))
resume("task-1")
print(is_paused("task-1"))

# TODO: 第二段演示（实现后补）
