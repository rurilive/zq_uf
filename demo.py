import os
import shutil
import tempfile
import time

from fileindex import changed, index, reset


def main():
    root = tempfile.mkdtemp()
    try:
        reset()
        a_path = os.path.join(root, "a.parquet")
        b_path = os.path.join(root, "b.parquet")
        gone_path = os.path.join(root, "gone.parquet")
        for path, content in ((a_path, "AAA"), (b_path, "BBB"), (gone_path, "GGG")):
            with open(path, "w") as handle:
                handle.write(content)

        first = index(root)
        print("第一次扫描:", first)

        time.sleep(0.01)
        future = time.time() + 10
        os.utime(a_path, (future, future))
        with open(b_path, "w") as handle:
            handle.write("XXX")
        os.remove(gone_path)
        with open(os.path.join(root, "new.parquet"), "w") as handle:
            handle.write("NNN")

        print("重扫前 a 仅改时间 changed:", changed(a_path))
        second = index(root)
        print("第二次扫描:", second)
        print("重扫后 a changed:", changed(a_path))
        print("重扫后 b changed:", changed(b_path))
        return first, second
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
