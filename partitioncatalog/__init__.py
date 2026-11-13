from .catalog import (
    ROOT,
    get,
    init,
    list_partitions,
    read_events,
    register,
    remove,
)

list = list_partitions

__all__ = [
    "ROOT",
    "get",
    "init",
    "list",
    "list_partitions",
    "read_events",
    "register",
    "remove",
]

