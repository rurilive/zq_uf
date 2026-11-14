from blockvote import allow, observe

observe("shop", "403")
print("allowed", allow("shop"))
observe("shop", "200")
print("allowed", allow("shop"))

# TODO: 第二段演示（实现后补）
