from dagrun import run

DAG = {"extract": lambda: "raw", "transform": lambda: "clean"}
print(run(DAG))

# TODO: 第二段演示（实现后补）
