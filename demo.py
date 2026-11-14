from schemamigrate import (
    current,
    downgrade,
    insert,
    migration_log,
    reset,
    rows,
    upgrade,
)

reset()
insert({"name": "Ada Lovelace", "contact": "ada@example.com"})
insert({"name": "Alan Turing", "contact": "alan@example.com"})

print("current:", current())

up = upgrade(3, operator="demo")
print("upgrade ok:", up["ok"], "version:", up["version"])
for step in up["steps"]:
    print(
        "  %d -> %d rows=%d checksum=%s... rollback_to=%d"
        % (
            step["from"],
            step["to"],
            step["rows_after"],
            step["checksum_after"][:12],
            step["rollback_point"],
        )
    )
print("rows@3:", rows())

down = downgrade(1, operator="demo")
print("downgrade ok:", down["ok"], "version:", down["version"])
print("rows@1:", rows())

print("log:")
for record in migration_log():
    print(
        "  %s -> v%d by %s in %.6fs (ok=%s)"
        % (
            record["action"],
            record["version"],
            record["operator"],
            record["duration"],
            record["ok"],
        )
    )
