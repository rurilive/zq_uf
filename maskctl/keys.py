"""版本化密钥管理：新数据用当前版本，历史令牌按各自版本还原。"""
import os


class KeyRing:
    """按版本保存处理密钥。轮换后旧版本保留，历史令牌仍可还原。"""

    def __init__(self, keys=None):
        self._keys = dict(keys or {})
        if not self._keys:
            self.add_key()

    @property
    def current_version(self):
        return max(self._keys)

    def add_key(self, key=None):
        """轮换出新版本密钥，返回版本号。"""
        version = self.current_version + 1 if self._keys else 1
        self._keys[version] = key or os.urandom(32)
        return version

    def get(self, version):
        try:
            return self._keys[int(version)]
        except (KeyError, ValueError, TypeError):
            raise KeyError(f"unknown key version: {version}")
