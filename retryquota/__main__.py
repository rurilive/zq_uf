"""跨进程查看协调状态：``python -m retryquota <state.json>``。"""

from __future__ import annotations

import argparse
import json
import sys

from .quota import get_coordinator, read_state, snapshot


def _advance(state_path):
    """用状态文件里的权威限制构造协调器，推进一次并返回快照。"""
    state = read_state(state_path)
    if state is None:
        return None
    from .quota import QuotaCoordinator

    quota = QuotaCoordinator(
        global_limit=state.get("global_limit"),
        site_limit=state.get("site_limit"),
        site_limits=state.get("site_limits"),
        lease_seconds=state.get("lease_seconds"),
        priorities=state.get("priorities"),
        state_path=state_path,
    )
    return quota.snapshot()


def main(argv=None):
    parser = argparse.ArgumentParser(description="查看 retryquota 协调状态")
    parser.add_argument(
        "state_path",
        nargs="?",
        help="协调状态文件；省略时查看当前进程的默认协调器",
    )
    parser.add_argument(
        "--json", action="store_true", help="输出原始 JSON（默认是缩进 JSON）"
    )
    parser.add_argument(
        "--events", type=int, default=None, help="只显示最近 N 条事件"
    )
    parser.add_argument(
        "--advance",
        action="store_true",
        help="读取状态文件时先归还超时许可并补发队列（会写回状态文件）",
    )
    args = parser.parse_args(argv)

    if args.state_path:
        if args.advance:
            view = _advance(args.state_path)
        else:
            view = read_state(args.state_path)
            if view is not None and args.events is not None:
                view = dict(view)
                view["events"] = view["events"][-args.events :]
        if view is None:
            print(f"没有可读的协调状态：{args.state_path}", file=sys.stderr)
            return 1
    else:
        view = snapshot()
        if args.events is not None:
            view["events"] = get_coordinator().events(limit=args.events)

    if args.json:
        print(json.dumps(view, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(view, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
