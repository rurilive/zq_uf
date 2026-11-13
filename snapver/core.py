"""整目录拷贝的快照。"""
import os
import shutil


class Snapshot:
    def __init__(self, root, store):
        self.root = root
        self.store = store

    def create(self, tag):
        target = os.path.join(self.store, tag)
        if os.path.exists(target):
            shutil.rmtree(target)
        shutil.copytree(self.root, target)
        return target

    def restore(self, tag):
        source = os.path.join(self.store, tag)
        for name in os.listdir(self.root):
            path = os.path.join(self.root, name)
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        for name in os.listdir(source):
            src = os.path.join(source, name)
            dst = os.path.join(self.root, name)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        return self.root
