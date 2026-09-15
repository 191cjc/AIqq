import unittest
from datetime import date, datetime, timezone

from aiqq.logic.history_metrics import (
    parse_history_metric_line,
    reporting_windows,
    summarize_history_metrics,
)


def line(timestamp, *, truncated, reached, source, dropped):
    return (
        f"{timestamp}\t[INFO]\taiqq.logic.group_context\t"
        "event=group_history_context_prepared request_id=req "
        "candidate_message_count=50 included_message_count=8 "
        f"source_char_count={source} included_char_count=1000 "
        "dropped_message_count=42 "
        f"dropped_char_count={dropped} truncated={str(truncated).lower()} "
        f"message_limit_reached={str(reached).lower()} char_limit=1000"
    )


class HistoryMetricsTests(unittest.TestCase):
    def test_parse_and_aggregate_daily_and_seven_day_metrics(self):
        events = tuple(
            parse_history_metric_line(value, default_timezone=timezone.utc)
            for value in (
                line(
                    "2026-09-09 12:00:00,000",
                    truncated=True,
                    reached=True,
                    source=3000,
                    dropped=2000,
                ),
                line(
                    "2026-09-08 12:00:00,000",
                    truncated=False,
                    reached=False,
                    source=500,
                    dropped=0,
                ),
            )
        )
        windows = reporting_windows(date(2026, 9, 9), timezone.utc)
        today = summarize_history_metrics(
            events, start=windows[0][1], end=windows[0][2]
        )
        week = summarize_history_metrics(
            events, start=windows[1][1], end=windows[1][2]
        )
        self.assertEqual(today.prepared_context_count, 1)
        self.assertEqual(today.truncation_rate, 1.0)
        self.assertEqual(week.prepared_context_count, 2)
        self.assertEqual(week.truncation_rate, 0.5)
        self.assertEqual(week.average_source_char_count, 1750)
        self.assertEqual(week.maximum_source_char_count, 3000)

    def test_missing_timestamp_or_fields_is_ignored(self):
        self.assertIsNone(parse_history_metric_line("event=group_history_context_prepared"))
        self.assertIsNone(parse_history_metric_line("2026-09-09 12:00:00 event=other"))


if __name__ == "__main__":
    unittest.main()
