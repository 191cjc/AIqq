import asyncio
import json
import unittest
from unittest.mock import patch

from aiqq.exceptions import (
    AgentUnavailable,
    InvalidAgentOutput,
    NovelAIPromptInvalidOutput,
    NovelAIPromptUnavailable,
    NovelAITagSearchUnavailable,
)
from aiqq.logic.models import DanbooruTag, NovelAIPromptOptions
from aiqq.logic.ports import AgentTurnResult
from aiqq.services.agents.novelai_prompt import NovelAIPromptAgent
from aiqq.services.agents.schemas import (
    NOVELAI_PROMPT_OPTIONS_OUTPUT_SCHEMA,
    NOVELAI_TAG_QUERIES_OUTPUT_SCHEMA,
)
from aiqq.services.novelai_tags import NovelAITagSearchService


OPTIONS = ("safe, sfw, white_cat", "safe, sfw, black_cat", "safe, sfw, sleeping_cat")


def plan(*queries):
    return json.dumps({"queries": list(queries)})


def options(prompts=OPTIONS):
    return json.dumps({"prompts": list(prompts)})


class FakeBackend:
    def __init__(self, responses, events=None):
        self.responses = list(responses)
        self.calls = []
        self.events = events if events is not None else []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        self.events.append("model")
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return AgentTurnResult(result)


class FakeSearch:
    def __init__(self, matches=None, events=None):
        self.matches = matches or {}
        self.calls = []
        self.events = events if events is not None else []

    async def search(self, query, *, limit=8):
        self.calls.append((query, limit))
        self.events.append(query)
        return self.matches.get(query, ())


class BlockingSearch:
    def __init__(self, expected, failure=None):
        self.expected = expected
        self.failure = failure
        self.started = set()
        self.finished = set()
        self.all_started = asyncio.Event()

    async def search(self, query, *, limit=8):
        self.started.add(query)
        if len(self.started) == self.expected:
            self.all_started.set()
        try:
            if query == "cat" and self.failure is not None:
                await self.all_started.wait()
                raise self.failure
            await asyncio.Future()
        finally:
            # A task must be awaited, not just cancelled, to finish async cleanup.
            await asyncio.sleep(0)
            self.finished.add(query)


class FakeTagTransport:
    def __init__(self, events):
        self.events = events
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        query = arguments["query"]
        self.events.append(query)
        tag, category = {
            "sora_harewataru": ("harewataru_sora", "character"),
            "hirogaru_sky!_precure": ("hirogaru_sky!_precure", "copyright"),
        }[query]
        return {"structuredContent": {"result": [
            {"tag": tag, "category": category, "post_count": 100},
            {"tag": "artist_name", "category": "artist", "post_count": 1000},
        ]}}


class NovelAIPromptAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_sora_generation_and_revision_preserve_title_through_real_tag_adapter(self):
        sora_options = (
            "safe, sfw, fully clothed, harewataru_sora, hirogaru_sky!_precure, standing",
            "safe, sfw, fully clothed, harewataru_sora, hirogaru_sky!_precure, smiling",
            "safe, sfw, fully clothed, harewataru_sora, hirogaru_sky!_precure, running",
        )
        for revision in (False, True):
            with self.subTest(revision=revision):
                events = []
                backend = FakeBackend([
                    plan("sora_harewataru", "hirogaru_sky!_precure"),
                    options(sora_options),
                ], events)
                transport = FakeTagTransport(events)
                agent = NovelAIPromptAgent(
                    backend, tag_search=NovelAITagSearchService(transport)
                )
                if revision:
                    result = await agent.revise(
                        NovelAIPromptOptions(OPTIONS), "改成光之美少女的索拉"
                    )
                else:
                    result = await agent.generate("光之美少女的索拉")
                self.assertEqual(result.prompts, sora_options)
                self.assertEqual(events, [
                    "model", "sora_harewataru", "hirogaru_sky!_precure", "model",
                ])
                self.assertEqual(transport.calls, [
                    ("suggest_tags", {"query": "sora_harewataru", "limit": 8}),
                    ("suggest_tags", {"query": "hirogaru_sky!_precure", "limit": 8}),
                ])
                planner = json.loads(backend.calls[0]["model_input"])
                author = json.loads(backend.calls[1]["model_input"])
                for key, value in planner.items():
                    self.assertEqual(author[key], value)
                self.assertEqual(author["tag_reference"], [
                    {"query": "sora_harewataru", "candidates": [
                        {"tag": "harewataru_sora", "category": "character", "post_count": 100},
                    ]},
                    {"query": "hirogaru_sky!_precure", "candidates": [
                        {"tag": "hirogaru_sky!_precure", "category": "copyright", "post_count": 100},
                    ]},
                ])

    async def test_generation_searches_before_authoring_and_passes_typed_references(self):
        events = []
        backend = FakeBackend([plan("Blue Hair", "blue_hair", "cat"), options()], events)
        search = FakeSearch({
            "blue_hair": (DanbooruTag("blue_hair", "general", 1200),),
            "cat": (
                DanbooruTag("cat", "general", 900),
                DanbooruTag("cat_character", "character", 90),
            ),
        }, events)
        with self.assertLogs("aiqq.services.agents.novelai_prompt", level="INFO") as captured:
            result = await NovelAIPromptAgent(backend, tag_search=search).generate("蓝头发和猫")

        self.assertEqual(result.prompts, OPTIONS)
        self.assertEqual([record.getMessage() for record in captured.records], [
            "event=novelai_tag_lookup_finished query_count=2 candidate_count=3",
        ])
        self.assertEqual(events, ["model", "blue_hair", "cat", "model"])
        self.assertEqual(search.calls, [("blue_hair", 8), ("cat", 8)])
        planner, author = backend.calls
        self.assertEqual(planner["output_schema"], NOVELAI_TAG_QUERIES_OUTPUT_SCHEMA)
        self.assertEqual(author["output_schema"], NOVELAI_PROMPT_OPTIONS_OUTPUT_SCHEMA)
        original = json.loads(planner["model_input"])
        grounded = json.loads(author["model_input"])
        self.assertEqual(original, {
            "protocol_version": 2,
            "operation": "novelai_prompt_generation",
            "description": "蓝头发和猫",
        })
        for key, value in original.items():
            self.assertEqual(grounded[key], value)
        self.assertEqual(grounded["tag_reference"], [
            {"query": "blue_hair", "candidates": [
                {"tag": "blue_hair", "category": "general", "post_count": 1200},
            ]},
            {"query": "cat", "candidates": [
                {"tag": "cat", "category": "general", "post_count": 900},
                {"tag": "cat_character", "category": "character", "post_count": 90},
            ]},
        ])

    async def test_revision_replans_latest_request_without_reusing_previous_references(self):
        backend = FakeBackend([
            plan("cat"), options(),
            plan("dog", "night"), options(("safe, dog", "safe, white_dog", "safe, black_dog")),
        ])
        search = FakeSearch({
            "cat": (DanbooruTag("cat", "general", 100),),
            "dog": (DanbooruTag("dog", "general", 200),),
        })
        agent = NovelAIPromptAgent(backend, tag_search=search)
        original = await agent.generate("cat")
        revised = await agent.revise(original, "改成夜晚的狗")

        self.assertEqual(revised.prompts[0], "safe, dog")
        self.assertEqual(search.calls, [("cat", 8), ("dog", 8), ("night", 8)])
        revision_plan = json.loads(backend.calls[2]["model_input"])
        self.assertEqual(revision_plan, {
            "protocol_version": 2,
            "operation": "novelai_prompt_revision",
            "existing_prompts": list(OPTIONS),
            "request": "改成夜晚的狗",
        })
        references = json.loads(backend.calls[3]["model_input"])["tag_reference"]
        self.assertEqual([entry["query"] for entry in references], ["dog", "night"])
        self.assertEqual(references[1]["candidates"], [])

    async def test_successful_no_match_still_authors_with_explicit_empty_candidates(self):
        backend = FakeBackend([plan("unusual_scene"), options()])
        search = FakeSearch()
        result = await NovelAIPromptAgent(backend, tag_search=search).generate("unusual scene")

        self.assertEqual(result.prompts, OPTIONS)
        self.assertEqual(search.calls, [("unusual_scene", 8)])
        self.assertEqual(json.loads(backend.calls[1]["model_input"])["tag_reference"], [
            {"query": "unusual_scene", "candidates": []},
        ])

    async def test_invalid_plans_stop_before_search_or_final_authoring(self):
        for response in ("not JSON", plan(), plan("cat,dog"), plan("猫")):
            with self.subTest(response=response):
                backend = FakeBackend([response, options()])
                search = FakeSearch()
                with self.assertRaises(NovelAIPromptInvalidOutput) as caught:
                    await NovelAIPromptAgent(backend, tag_search=search).generate("cat")
                self.assertEqual(caught.exception.stage, "planning")
                self.assertNotIsInstance(caught.exception, NovelAITagSearchUnavailable)
                self.assertEqual(search.calls, [])
                self.assertEqual(len(backend.calls), 1)

    async def test_model_failure_at_either_stage_remains_prompt_failure(self):
        secret = "https://private.test/?token=secret member-id group-id private prompt"
        for stage, responses, expected_searches in (
            ("planning", [AgentUnavailable(secret)], []),
            ("authoring", [plan("cat"), AgentUnavailable(secret)], [("cat", 8)]),
        ):
            with self.subTest(stage=stage):
                backend = FakeBackend(responses)
                search = FakeSearch()
                with self.assertLogs("aiqq.services.agents.novelai_prompt") as captured:
                    with self.assertRaises(NovelAIPromptUnavailable) as caught:
                        await NovelAIPromptAgent(backend, tag_search=search).generate(secret)
                self.assertNotIsInstance(caught.exception, NovelAIPromptInvalidOutput)
                self.assertNotIsInstance(caught.exception, NovelAITagSearchUnavailable)
                self.assertEqual(search.calls, expected_searches)
                expected_events = [] if stage == "planning" else [
                    "event=novelai_tag_lookup_finished query_count=1 candidate_count=0",
                ]
                expected_events.append(
                    f"event=novelai_prompt_failed stage={stage} kind=unavailable error_type=AgentUnavailable"
                )
                self.assertEqual([record.getMessage() for record in captured.records], expected_events)
                self.assertNotIn(secret, str(caught.exception))
                self.assertTrue(all(record.exc_info is None for record in captured.records))

    async def test_invalid_outputs_report_stage_without_logging_sensitive_details(self):
        secret = "https://private.test/?token=secret member-id group-id private prompt"
        cases = (
            ("planning", [plan("rating:private_query")], []),
            ("planning", [InvalidAgentOutput("private_agent", secret)], []),
            ("authoring", [plan("private_query"), secret], [("private_query", 8)]),
            ("authoring", [plan("private_query"), options(("safe, 猫", "safe, dog", "safe, bird"))], [("private_query", 8)]),
            ("authoring", [plan("private_query"), InvalidAgentOutput("private_agent", secret)], [("private_query", 8)]),
        )
        for revision in (False, True):
            for stage, responses, expected_searches in cases:
                with self.subTest(revision=revision, stage=stage, response=type(responses[-1])):
                    backend = FakeBackend(responses)
                    search = FakeSearch()
                    agent = NovelAIPromptAgent(backend, tag_search=search)
                    with self.assertLogs("aiqq.services.agents.novelai_prompt") as captured:
                        with self.assertRaises(NovelAIPromptInvalidOutput) as caught:
                            if revision:
                                await agent.revise(NovelAIPromptOptions(OPTIONS), secret)
                            else:
                                await agent.generate(secret)
                    self.assertEqual(caught.exception.stage, stage)
                    self.assertEqual(search.calls, expected_searches)
                    self.assertEqual(len(backend.calls), 1 if stage == "planning" else 2)
                    expected_events = [] if stage == "planning" else [
                        "event=novelai_tag_lookup_finished query_count=1 candidate_count=0",
                    ]
                    expected_events.append(
                        f"event=novelai_prompt_failed stage={stage} kind=invalid_output error_type=InvalidAgentOutput"
                    )
                    self.assertEqual([record.getMessage() for record in captured.records], expected_events)
                    self.assertTrue(all(record.exc_info is None for record in captured.records))
                    self.assertNotIn(secret, str(caught.exception))

    def test_invalid_output_stage_is_bounded(self):
        with self.assertRaises(ValueError):
            NovelAIPromptInvalidOutput(stage="private untrusted stage")

    async def test_lookup_failure_preserves_error_and_awaits_sibling_cleanup_on_both_paths(self):
        for revision in (False, True):
            with self.subTest(revision=revision):
                failure = NovelAITagSearchUnavailable("lookup unavailable")
                search = BlockingSearch(expected=3, failure=failure)
                backend = FakeBackend([plan("cat", "dog", "bird"), options()])
                agent = NovelAIPromptAgent(backend, tag_search=search)
                with self.assertRaises(NovelAITagSearchUnavailable) as caught:
                    if revision:
                        await agent.revise(NovelAIPromptOptions(OPTIONS), "add a bird")
                    else:
                        await agent.generate("animals")
                self.assertIs(caught.exception, failure)
                self.assertEqual(search.finished, {"cat", "dog", "bird"})
                self.assertEqual(len(backend.calls), 1)

    async def test_batch_timeout_is_lookup_failure_and_awaits_cleanup(self):
        search = BlockingSearch(expected=2)
        backend = FakeBackend([plan("cat", "dog"), options()])
        with patch(
            "aiqq.services.agents.novelai_prompt.TAG_SEARCH_BATCH_TIMEOUT_SECONDS", 0.02
        ):
            with self.assertRaises(NovelAITagSearchUnavailable):
                await NovelAIPromptAgent(backend, tag_search=search).generate("animals")
        self.assertEqual(search.finished, {"cat", "dog"})
        self.assertEqual(len(backend.calls), 1)

    async def test_cancellation_propagates_after_all_lookup_tasks_finish(self):
        search = BlockingSearch(expected=2)
        backend = FakeBackend([plan("cat", "dog"), options()])
        task = asyncio.create_task(
            NovelAIPromptAgent(backend, tag_search=search).generate("animals")
        )
        try:
            await asyncio.wait_for(search.all_started.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(search.finished, {"cat", "dog"})
        self.assertEqual(len(backend.calls), 1)


if __name__ == "__main__":
    unittest.main()
