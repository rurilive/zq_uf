import shutil
import tempfile
import os

from partitioncatalog import catalog, list_partitions, register

cwd = os.getcwd()
os.chdir(tempfile.mkdtemp())
register({"dataset": "orders", "name": "dt=2026-01-01"})
register({"dataset": "orders", "name": "dt=2026-01-02"})
print(list_partitions("orders"))
os.chdir(cwd)

# TODO: 第二段演示（实现后补）
