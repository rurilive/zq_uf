import json

from confdrift import diff, merge_alerts, snapshot

SENSITIVE = ["token"]

baseline = snapshot("repo-baseline", {
    "workers": 4, "token": "abc", "retries": 3, "endpoint": "https://api.internal",
}, sensitive=SENSITIVE)
prod = snapshot("prod", {
    "workers": 8, "token": "xyz", "retries": 3, "endpoint": "https://api.internal",
    "debug": True,
}, sensitive=SENSITIVE)
staging = snapshot("staging", {
    "workers": "4", "token": "abc", "endpoint": "https://api.internal",
}, sensitive=SENSITIVE)

reports = [diff(baseline, prod), diff(baseline, staging)]
for report in reports:
    print("%s vs %s:" % (report["base_env"], report["env"]))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=repr))

print("merged alerts:")
print(json.dumps(merge_alerts(*reports), ensure_ascii=False, indent=2))
