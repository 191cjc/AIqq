import asyncio
import json
import unittest
from unittest.mock import patch

from aiqq.exceptions import NovelAITagSearchUnavailable
from aiqq.logic.models import DanbooruTag
from aiqq.services.novelai_tags import NovelAITagSearchService


def tag(name="blue_hair", category="general", count=100, **extra):
    return {"tag": name, "category": category, "post_count": count, **extra}


def structured(records):
    return {"isError": False, "structuredContent": {"result": records}}


def text_response(*values):
    return {
        "content": [
            {"type": "text", "text": json.dumps(value)} for value in values
        ]
    }


class FakeMCPClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class BlockingMCPClient:
    def __init__(self):
        self.active = 0
        self.peak_active = 0
        self.cancelled = 0
        self.calls = []
        self.entered = asyncio.Event()
        self.three_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        self.active += 1
        self.peak_active = max(self.active, self.peak_active)
        self.entered.set()
        if self.active == 3:
            self.three_entered.set()
        try:
            await self.release.wait()
            return structured([tag()])
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1


class NovelAITagSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_observed_schema_prefers_structured_content_and_strips_extra_fields(self):
        response = structured([
            tag(label="Private label", url="https://private.test/?token=secret"),
        ])
        response["content"] = [{"type": "text", "text": "unused fallback"}]
        client = FakeMCPClient(response)

        result = await NovelAITagSearchService(client).search(" blue hair ")

        self.assertEqual(result, (DanbooruTag("blue_hair", "general", 100),))
        self.assertEqual(client.calls, [("suggest_tags", {"query": "blue_hair", "limit": 8})])

    async def test_text_json_record_array_and_result_wrappers(self):
        records = [tag(), tag("blue_eyes", count=90)]
        for response in (
            text_response(*records),
            text_response(records),
            text_response({"result": records}),
            text_response([records[0]], {"result": [records[1]]}),
        ):
            with self.subTest(response=response):
                result = await NovelAITagSearchService(FakeMCPClient(response)).search("blue")
                self.assertEqual(result, (
                    DanbooruTag("blue_hair", "general", 100),
                    DanbooruTag("blue_eyes", "general", 90),
                ))

    async def test_empty_matches_and_artist_or_meta_only_are_successful_empty_results(self):
        for response in (
            structured([]), text_response([]), text_response({"result": []}),
            structured([tag("artist_name", "artist"), tag("best_quality", "meta")]),
        ):
            with self.subTest(response=response):
                self.assertEqual(
                    await NovelAITagSearchService(FakeMCPClient(response)).search("blue"),
                    (),
                )

    async def test_filters_deduplicates_orders_and_limits_candidates(self):
        records = [
            tag("blue_hair", count=10), tag("artist_name", "artist", 999),
            tag("best_quality", "meta", 999), tag("blue_hair", count=100),
            tag("character_name", "character", 80),
            tag("copyright_name", "copyright", 50), tag("blue_eyes", count=50),
        ]
        client = FakeMCPClient(structured(records))
        self.assertEqual(await NovelAITagSearchService(client).search("blue", limit=3), (
            DanbooruTag("blue_hair", "general", 100),
            DanbooruTag("character_name", "character", 80),
            DanbooruTag("blue_eyes", "general", 50),
        ))
        self.assertEqual(client.calls[0][1]["limit"], 3)

    async def test_malformed_and_error_envelopes_fail_without_fallback(self):
        responses = (
            None, [], {}, {"content": []}, structured(None),
            {"structuredContent": None, **text_response([tag()])},
            {"structuredContent": {}, **text_response([tag()])},
            {"structuredContent": {"result": [], "error": "private"}},
            {"isError": True, **text_response([tag()])},
            {"isError": "false", **text_response([tag()])},
            {"error": "private", **text_response([tag()])},
            text_response({"result": [], "isError": True}),
            text_response({"error": "private"}),
            text_response(None), text_response("no matches"), text_response({}),
            {"content": [{"type": "text", "text": "not json"}]},
            {"content": [{"type": "text", "text": 1}]},
            {"content": [{"type": "image", "data": "private"}]},
            {"content": [None]},
        )
        for response in responses:
            with self.subTest(response=response):
                with self.assertLogs("aiqq.services.novelai_tags"):
                    with self.assertRaises(NovelAITagSearchUnavailable):
                        await NovelAITagSearchService(FakeMCPClient(response)).search("blue")

    async def test_invalid_records_fail_instead_of_becoming_empty_matches(self):
        invalid = [
            None, {}, tag(""), tag("x" * 121), tag("非英文"),
            tag("blue,hair"), tag("blue\nhair"), tag(" blue_hair"),
            tag(category=None), tag(category=[]), tag(category="unknown"),
            tag(count=-1), tag(count=True), tag(count="100"), tag(count=1.5),
            tag(error="private"),
        ]
        for record in invalid:
            with self.subTest(record=record):
                with self.assertLogs("aiqq.services.novelai_tags"):
                    with self.assertRaises(NovelAITagSearchUnavailable):
                        await NovelAITagSearchService(FakeMCPClient(structured([record]))).search("blue")

    async def test_response_record_and_text_size_limits(self):
        responses = (
            structured([tag()] * 101),
            text_response([tag()] * 101),
            text_response(*([tag()] * 101)),
            text_response([tag()] * 60, [tag()] * 60),
            text_response(tag(label="x" * 65_536)),
        )
        for response in responses:
            with self.subTest(response_type=type(response)):
                with self.assertLogs("aiqq.services.novelai_tags"):
                    with self.assertRaises(NovelAITagSearchUnavailable):
                        await NovelAITagSearchService(FakeMCPClient(response)).search("blue")

    async def test_invalid_query_and_limit_do_not_call_transport(self):
        client = FakeMCPClient(structured([]))
        service = NovelAITagSearchService(client)
        for query in (
            "", " ", None, "x" * 81, "非英文", "blue,hair", "blue\nhair",
            "rating:general", "blue*", "-blue_hair", "blue\thair", "blue|hair",
            "!blue_hair", "blue?", "blue&hair", "blue;hair", "blue=hair", "123",
            "blue\x7fhair", "~blue", "blue\\hair", " " + "x" * 80,
        ):
            with self.subTest(query=query):
                with self.assertLogs("aiqq.services.novelai_tags"):
                    with self.assertRaises(NovelAITagSearchUnavailable):
                        await service.search(query)
        for limit in (0, -1, 21, 1.5, True, None):
            with self.subTest(limit=limit):
                with self.assertLogs("aiqq.services.novelai_tags"):
                    with self.assertRaises(NovelAITagSearchUnavailable):
                        await service.search("blue", limit=limit)
        self.assertEqual(client.calls, [])

    async def test_typical_tag_punctuation_is_supported(self):
        client = FakeMCPClient(structured([]))
        service = NovelAITagSearchService(client)
        queries = (
            "1girl", "character_(series)", "fate/grand_order", "character's_hat",
            "a+b-c.d", "hirogaru_sky!_precure", "bang_dream!", "x" * 80,
        )
        for query in queries:
            await service.search(query)
        self.assertEqual(client.calls, [
            ("suggest_tags", {"query": query, "limit": 8}) for query in queries
        ])

    async def test_query_normalization_preserves_title_punctuation(self):
        client = FakeMCPClient(structured([]))
        await NovelAITagSearchService(client).search(" Hirogaru  Sky! Precure ")
        self.assertEqual(client.calls, [
            ("suggest_tags", {"query": "hirogaru_sky!_precure", "limit": 8}),
        ])

    async def test_unconfigured_and_transport_failures_are_typed_and_sanitized(self):
        secret = "https://private.test/?token=secret private prompt"
        services = (
            NovelAITagSearchService(None),
            NovelAITagSearchService(FakeMCPClient(RuntimeError(secret))),
            NovelAITagSearchService(FakeMCPClient({"isError": True, "error": secret})),
        )
        for service in services:
            with self.subTest(service=service):
                with self.assertLogs("aiqq.services.novelai_tags") as logs:
                    with self.assertRaises(NovelAITagSearchUnavailable) as raised:
                        await service.search("private_query")
                self.assertNotIn(secret, "\n".join(logs.output))
                self.assertNotIn("private_query", "\n".join(logs.output))
                self.assertEqual(str(raised.exception), "Danbooru tag lookup failed")

    async def test_transport_timeout_cancels_request_and_releases_slot(self):
        client = BlockingMCPClient()
        service = NovelAITagSearchService(client)
        with patch("aiqq.services.novelai_tags.TAG_SEARCH_TIMEOUT_SECONDS", 0.01):
            with self.assertLogs("aiqq.services.novelai_tags") as logs:
                with self.assertRaises(NovelAITagSearchUnavailable):
                    await service.search("blue")
        self.assertIn("error_type=TimeoutError", "\n".join(logs.output))
        self.assertEqual(client.cancelled, 1)
        self.assertEqual(client.active, 0)
        client.release.set()
        self.assertTrue(await service.search("blue"))

    async def test_concurrent_requests_are_bounded_to_three(self):
        client = BlockingMCPClient()
        service = NovelAITagSearchService(client)
        tasks = [asyncio.create_task(service.search(f"concept_{i}")) for i in range(8)]
        try:
            await asyncio.wait_for(client.three_entered.wait(), timeout=1)
            self.assertEqual(len(client.calls), 3)
            self.assertEqual(client.peak_active, 3)
            client.release.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)
            self.assertEqual(len(results), 8)
            self.assertEqual(client.peak_active, 3)
            self.assertEqual(client.active, 0)
            self.assertTrue(all(name == "suggest_tags" for name, _ in client.calls))
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_cancellation_propagates_in_flight_and_while_queued(self):
        client = BlockingMCPClient()
        service = NovelAITagSearchService(client)
        tasks = [asyncio.create_task(service.search(f"concept_{i}")) for i in range(4)]
        try:
            await asyncio.wait_for(client.three_entered.wait(), timeout=1)
            tasks[-1].cancel()
            with self.assertRaises(asyncio.CancelledError):
                await tasks[-1]
            self.assertEqual(len(client.calls), 3)
            tasks[0].cancel()
            with self.assertRaises(asyncio.CancelledError):
                await tasks[0]
            self.assertEqual(client.cancelled, 1)
            client.release.set()
            await asyncio.gather(*tasks[1:3])
            self.assertTrue(await service.search("blue"))
            self.assertEqual(client.active, 0)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
