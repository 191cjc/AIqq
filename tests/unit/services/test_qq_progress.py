import asyncio
import unittest
from datetime import datetime, timezone

from aiqq.interfaces.qq.progress import QQProgressReporter
from aiqq.logic.models import BotSentGroupMessage


class FakeSender:
    def __init__(self):
        self.events = []
        self.recall_succeeds = True
        self.next_id = 1

    async def send_text_reply(self, **kwargs):
        target = BotSentGroupMessage(
            kwargs["group_id"],
            f"progress-{self.next_id}",
            datetime.now(timezone.utc),
            True,
        )
        self.next_id += 1
        self.events.append(("send", kwargs["content"], kwargs["msg_seq"]))
        return target

    async def recall_from_group(self, target):
        self.events.append(("recall", target.message_id))
        return self.recall_succeeds


class QQProgressReporterTests(unittest.IsolatedAsyncioTestCase):
    async def test_next_progress_recalls_previous_before_sending(self):
        sender = FakeSender()
        reporter = QQProgressReporter(
            sender,
            group_id="group-1",
            source_message_id="user-1",
            cleanup_seconds=60,
        )
        await reporter.publish("stage A")
        await reporter.publish("stage B")
        await reporter.finish()
        await asyncio.sleep(0)

        self.assertEqual(
            sender.events,
            [
                ("send", "stage A", 1),
                ("recall", "progress-1"),
                ("send", "stage B", 2),
                ("recall", "progress-2"),
            ],
        )
        self.assertEqual(reporter.next_msg_seq, 3)

    async def test_recall_failure_prevents_another_progress_message(self):
        sender = FakeSender()
        reporter = QQProgressReporter(
            sender,
            group_id="group-1",
            source_message_id="user-1",
            cleanup_seconds=60,
        )
        await reporter.publish("stage A")
        sender.recall_succeeds = False
        await reporter.publish("stage B")
        await reporter.finish()
        await asyncio.sleep(0)

        self.assertEqual(
            [event for event in sender.events if event[0] == "send"],
            [("send", "stage A", 1)],
        )

    async def test_concurrent_updates_never_send_without_recall_between_them(self):
        sender = FakeSender()
        reporter = QQProgressReporter(
            sender,
            group_id="group-1",
            source_message_id="user-1",
            cleanup_seconds=60,
        )
        await asyncio.gather(reporter.publish("A"), reporter.publish("B"))
        await reporter.finish()
        await asyncio.sleep(0)
        self.assertEqual(sender.events[1][0], "recall")
        self.assertEqual(sender.events[2][0], "send")


if __name__ == "__main__":
    unittest.main()
