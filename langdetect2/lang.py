"""兼容层：历史调用方从 langdetect2.lang 导入 detect/confident。

实现已迁移到分层模块，见 detector.py。
"""

from .detector import confident, detect

__all__ = ["detect", "confident"]
