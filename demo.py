from partretention import apply, plan

p = plan({"name": "orders", "days": ["2025-01-01", "2025-01-02"]})
print(p)
print(apply(p))

# TODO: 第二段演示（实现后补）
