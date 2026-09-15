#!/usr/bin/env python3
"""Report today's and trailing-seven-day group-history truncation metrics."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from aiqq.logic.history_metrics import (
    parse_history_metric_line,
    reporting_windows,
    summarize_history_metrics,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="统计 AiQQ 群历史 1000 字预算的裁剪触发情况。"
    )
    parser.add_argument(
        "logs",
        type=Path,
        nargs="*",
        default=[Path("aiqq.log")],
        help="日志文件，可同时指定轮转文件；默认 aiqq.log。",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=date.today(),
        help="统计基准自然日，格式 YYYY-MM-DD，默认今天。",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON。")
    return parser.parse_args(argv)


def load_events(paths: list[Path]):
    timezone = datetime.now().astimezone().tzinfo
    events = []
    for path in paths:
        try:
            with path.expanduser().open("r", encoding="utf-8", errors="replace") as log:
                for line in log:
                    event = parse_history_metric_line(
                        line, default_timezone=timezone
                    )
                    if event is not None:
                        events.append(event)
        except OSError as exc:
            raise RuntimeError(f"无法读取日志 {path}: {exc}") from exc
    return tuple(events)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        events = load_events(args.logs)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    timezone = datetime.now().astimezone().tzinfo
    reports = {
        name: asdict(summarize_history_metrics(events, start=start, end=end))
        for name, start, end in reporting_windows(args.date, timezone)
    }
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        for label, title in (("today", "当日"), ("last_7_days", "最近 7 天")):
            report = reports[label]
            print(
                f"{title}: 构建 {report['prepared_context_count']} 次，"
                f"裁剪 {report['truncated_context_count']} 次，"
                f"触发率 {report['truncation_rate']:.2%}，"
                f"达到 50 条 {report['message_limit_reached_count']} 次，"
                f"源字符平均/最大 {report['average_source_char_count']:.1f}/"
                f"{report['maximum_source_char_count']}，"
                f"平均丢弃 {report['average_dropped_char_count']:.1f} 字"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
