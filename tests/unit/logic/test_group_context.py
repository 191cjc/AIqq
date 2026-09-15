import logging
import unittest

from aiqq.logic.group_context import TRUNCATION_MARKER, prepare_group_context
from aiqq.logic.models import GroupHistoryMessage


def message(record_id, content, sender="member"):
    return GroupHistoryMessage(
        record_id=record_id,
        role="user",
        sender_name=sender,
        sent_at=f"2026-09-09T12:00:{record_id:02d}+08:00",
        content=content,
    )


class GroupContextTests(unittest.TestCase):
    def test_prepared_event_is_logged_without_truncation(self):
        with self.assertLogs("test.group_context", level="INFO") as captured:
            result = prepare_group_context(
                (message(1, "hello"),),
                request_id="request-1",
                char_limit=1000,
                event_logger=logging.getLogger("test.group_context"),
            )

        self.assertFalse(result.stats.truncated)
        self.assertIn("event=group_history_context_prepared", captured.output[0])
        self.assertIn("truncated=false", captured.output[0])
        self.assertNotIn("hello", captured.output[0])
        self.assertNotIn("member", captured.output[0])

    def test_budget_keeps_newest_messages_and_records_consistent_counts(self):
        with self.assertLogs("test.group_context.trim", level="INFO"):
            result = prepare_group_context(
                (message(1, "a" * 60), message(2, "b" * 60)),
                request_id="request-2",
                char_limit=80,
                event_logger=logging.getLogger("test.group_context.trim"),
            )

        self.assertTrue(result.stats.truncated)
        self.assertEqual(result.messages[-1].record_id, 2)
        self.assertEqual(result.messages[-1].content, "b" * 60)
        self.assertEqual(result.stats.source_char_count, 120)
        self.assertLessEqual(result.stats.included_char_count, 80)
        self.assertEqual(
            result.stats.dropped_char_count,
            result.stats.source_char_count - result.stats.included_char_count,
        )
        self.assertIn(TRUNCATION_MARKER, result.messages[0].content)

    def test_message_limit_is_independent_from_character_truncation(self):
        with self.assertLogs("test.group_context.limit", level="INFO"):
            result = prepare_group_context(
                tuple(message(index, "x") for index in range(1, 51)),
                request_id="request-3",
                message_limit=50,
                char_limit=1000,
                event_logger=logging.getLogger("test.group_context.limit"),
            )
        self.assertTrue(result.stats.message_limit_reached)
        self.assertFalse(result.stats.truncated)


if __name__ == "__main__":
    unittest.main()
