from authmatrix import matrix, report

print(matrix({"api": "/admin/users", "expected": "deny"}))
print(matrix({"api": "/admin/orders", "expected": "deny", "actual": "allow"}))
print(report())

# TODO: 第二段演示（实现后补）
