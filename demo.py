from schemamigrate import Migration, current, downgrade, migrator, register, upgrade

print(current())
print(upgrade(2))

# 第二段演示：注册双向迁移，双写兼容，升级再回退
migrator.tables["users"] = [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]


def to_fullname(rows):
    return [{"id": r["id"], "fullname": r["name"]} for r in rows]


def to_name(rows):
    return [{"id": r["id"], "name": r["fullname"]} for r in rows]


register(Migration(3, to_fullname, to_name, table="users",
                   aliases={"fullname": "name"}))

print(migrator.insert("users", {"id": 3, "fullname": "carol"}))  # 双写
print(migrator.read_field({"name": "alice"}, "fullname"))        # 读时兼容

up = upgrade(3)
down = downgrade(2)
for result in (up, down):
    print(result["direction"], "->", result["version"], "ok =", result["ok"],
          "rollback_point =", result["rollback_point"],
          "operator =", result["operator"],
          "duration_ms =", result["duration_ms"])
    for step in result["steps"]:
        print("  step", step["version"], step["direction"],
              step["rows_before"], "->", step["rows_after"],
              step["checksum_before"][:12], "->", step["checksum_after"][:12])
print(migrator.tables["users"])
