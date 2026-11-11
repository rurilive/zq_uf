from crawlflow import Scheduler

scheduler = Scheduler()
for url in ("https://example.com/a", "https://example.com/a", "https://example.com/b"):
    scheduler.add(url)
while True:
    task = scheduler.next()
    if task is None:
        break
    scheduler.success(task.url, "ok")
print("pending", scheduler.pending())
